"""Tests for per-DEX builder fee attachment.

Covers the three integration points:
- :meth:`OrderManager._builder_for_coin` returns the correct
  ``BuilderInfo`` based on the coin's DEX prefix
- :meth:`OrderManager._place_order` forwards the builder kwarg to
  ``Exchange.order``
- :meth:`OrderManager.bulk_place_orders` groups orders by DEX so each
  group gets its own builder, including the no-builder fall-back for
  standard Hyperliquid coins
- :meth:`OrderManager.approve_configured_builders` calls
  ``Exchange.approve_builder_fee`` per configured DEX and tolerates
  per-DEX failures
"""

from unittest.mock import MagicMock, patch
import pytest

from config import Config
from order_manager import OrderManager, Order, OrderSide
from rate_limiter import API_ERRORS


@pytest.fixture(autouse=True)
def _bypass_api_wrapper():
    with patch('order_manager.api_wrapper') as mock_wrapper:
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        yield mock_wrapper


@pytest.fixture
def cash_builder(monkeypatch):
    monkeypatch.setattr(Config, "BUILDER_FEES", {
        "cash": {
            "address": "0x4950994884602d1b6c6d96e4fe30f58205c39395",
            "tenths_bps": 10,
            "max_fee_rate": "0.05%",
        },
    })
    return Config.BUILDER_FEES["cash"]


@pytest.fixture
def two_builders(monkeypatch):
    monkeypatch.setattr(Config, "BUILDER_FEES", {
        "cash": {
            "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "tenths_bps": 10,
            "max_fee_rate": "0.05%",
        },
        "vntl": {
            "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "tenths_bps": 5,
            "max_fee_rate": "0.05%",
        },
    })


def _make_order_manager():
    exchange = MagicMock()
    info = MagicMock()
    return OrderManager(exchange, info, "0xabc")


def _make_order(coin: str = "BTC", side: OrderSide = OrderSide.BUY) -> Order:
    return Order(
        id=None, coin=coin, side=side, size=0.1, price=100.0,
        order_type={"limit": {"tif": "Alo"}}, reduce_only=False,
    )


class TestBuilderForCoin:
    def test_returns_builder_for_configured_dex(self, cash_builder):
        mgr = _make_order_manager()
        b = mgr._builder_for_coin("cash:HOOD")
        assert b == {"b": cash_builder["address"], "f": 10}

    def test_returns_none_for_unconfigured_dex(self, cash_builder):
        mgr = _make_order_manager()
        # cash is configured, xyz is not
        assert mgr._builder_for_coin("xyz:NVDA") is None

    def test_returns_none_for_standard_hl_coin(self, cash_builder):
        mgr = _make_order_manager()
        assert mgr._builder_for_coin("BTC") is None

    def test_returns_none_when_no_builders_configured(self, monkeypatch):
        monkeypatch.setattr(Config, "BUILDER_FEES", {})
        mgr = _make_order_manager()
        assert mgr._builder_for_coin("cash:HOOD") is None


class TestPlaceOrderForwardsBuilder:
    def _stub_order_response(self, exchange, oid: int = 42) -> None:
        exchange.order.return_value = {
            "status": "ok",
            "response": {"data": {"statuses": [{"resting": {"oid": oid}}]}},
        }

    def test_hip3_with_configured_dex_attaches_builder(self, cash_builder):
        mgr = _make_order_manager()
        self._stub_order_response(mgr.exchange)

        result = mgr._place_order(_make_order(coin="cash:HOOD"))

        assert result is not None
        # Positional: coin, is_buy, sz, px, type, reduce_only, cloid, builder
        call_args = mgr.exchange.order.call_args
        assert call_args.args[7] == {"b": cash_builder["address"], "f": 10}

    def test_hip3_without_configured_dex_passes_none(self, cash_builder):
        mgr = _make_order_manager()
        self._stub_order_response(mgr.exchange)

        mgr._place_order(_make_order(coin="xyz:NVDA"))

        call_args = mgr.exchange.order.call_args
        assert call_args.args[7] is None

    def test_standard_hl_passes_none(self, cash_builder):
        mgr = _make_order_manager()
        self._stub_order_response(mgr.exchange)

        mgr._place_order(_make_order(coin="BTC"))

        call_args = mgr.exchange.order.call_args
        assert call_args.args[7] is None


class TestBulkGrouping:
    def _make_bulk_response(self, oids: list) -> dict:
        return {
            "status": "ok",
            "response": {"data": {"statuses": [
                {"resting": {"oid": o}} for o in oids
            ]}},
        }

    def test_single_dex_one_call(self, cash_builder):
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.return_value = self._make_bulk_response([1, 2])

        orders = [_make_order(coin="cash:HOOD"), _make_order(coin="cash:INTC")]
        results = mgr.bulk_place_orders(orders)

        assert mgr.exchange.bulk_orders.call_count == 1
        # Builder attached to the single call
        builder_arg = mgr.exchange.bulk_orders.call_args.args[1]
        assert builder_arg == {"b": cash_builder["address"], "f": 10}
        assert [r.id for r in results] == [1, 2]

    def test_two_dex_two_calls_with_distinct_builders(self, two_builders):
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.side_effect = [
            self._make_bulk_response([1]),
            self._make_bulk_response([2]),
        ]

        orders = [_make_order(coin="cash:HOOD"), _make_order(coin="vntl:MAG7")]
        results = mgr.bulk_place_orders(orders)

        assert mgr.exchange.bulk_orders.call_count == 2
        # Build a {dex_address: tenths} map of what was passed
        sent_builders = [
            call.args[1] for call in mgr.exchange.bulk_orders.call_args_list
        ]
        sent_addresses = {b["b"] for b in sent_builders if b is not None}
        assert sent_addresses == {
            Config.BUILDER_FEES["cash"]["address"],
            Config.BUILDER_FEES["vntl"]["address"],
        }
        # Both orders eventually placed
        assert all(r is not None for r in results)

    def test_mixed_hl_and_hip3_passes_none_for_hl(self, cash_builder):
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.side_effect = [
            self._make_bulk_response([1]),
            self._make_bulk_response([2]),
        ]

        orders = [_make_order(coin="BTC"), _make_order(coin="cash:HOOD")]
        results = mgr.bulk_place_orders(orders)

        assert mgr.exchange.bulk_orders.call_count == 2
        sent_builders = [
            call.args[1] for call in mgr.exchange.bulk_orders.call_args_list
        ]
        assert None in sent_builders
        assert any(
            b is not None and b["b"] == cash_builder["address"]
            for b in sent_builders
        )
        assert all(r is not None for r in results)

    def test_result_order_preserved(self, two_builders):
        """Each input slot must receive the oid that came back for *its* coin.

        This is the regression we'd lose if the grouping/stitching code
        ever stopped tracking original indices.  We assign a deterministic
        oid per coin and assert positions in ``results`` line up with the
        positions in ``orders``, regardless of DEX iteration order.
        """
        mgr = _make_order_manager()
        coin_to_oid = {"cash:HOOD": 10, "cash:INTC": 30, "vntl:MAG7": 20}

        def fake_bulk(order_requests, builder=None):
            statuses = [
                {"resting": {"oid": coin_to_oid[r["coin"]]}}
                for r in order_requests
            ]
            return {"status": "ok", "response": {"data": {"statuses": statuses}}}

        mgr.exchange.bulk_orders.side_effect = fake_bulk

        orders = [
            _make_order(coin="cash:HOOD"),  # position 0
            _make_order(coin="vntl:MAG7"),  # position 1
            _make_order(coin="cash:INTC"),  # position 2
        ]
        results = mgr.bulk_place_orders(orders)

        # Each result is in its original input slot, with the matching oid.
        assert results[0] is not None and results[0].id == 10
        assert results[1] is not None and results[1].id == 20
        assert results[2] is not None and results[2].id == 30


class TestApproveConfiguredBuilders:
    def test_no_builders_makes_no_call(self, monkeypatch):
        monkeypatch.setattr(Config, "BUILDER_FEES", {})
        mgr = _make_order_manager()
        mgr.approve_configured_builders()
        mgr.exchange.approve_builder_fee.assert_not_called()

    def test_calls_per_configured_dex(self, two_builders):
        mgr = _make_order_manager()
        mgr.exchange.approve_builder_fee.return_value = {"status": "ok"}

        mgr.approve_configured_builders()

        assert mgr.exchange.approve_builder_fee.call_count == 2
        called_addresses = {
            call.args[0] for call in mgr.exchange.approve_builder_fee.call_args_list
        }
        assert called_addresses == {
            Config.BUILDER_FEES["cash"]["address"],
            Config.BUILDER_FEES["vntl"]["address"],
        }

    def test_failure_does_not_abort_remaining(self, two_builders):
        mgr = _make_order_manager()
        # First DEX raises, second succeeds.  Both should still be attempted.
        mgr.exchange.approve_builder_fee.side_effect = [
            API_ERRORS[0]("network fail"),
            {"status": "ok"},
        ]
        mgr.approve_configured_builders()
        assert mgr.exchange.approve_builder_fee.call_count == 2

    def test_warns_when_per_order_fee_exceeds_max_rate(self, monkeypatch, caplog):
        """tenths_bps=50 (5 bp) with max_fee_rate=0.001% (0.1 bp) is a
        misconfiguration where every order would be rejected.  The startup
        validator must warn so operators see the cause in the bot log."""
        monkeypatch.setattr(Config, "BUILDER_FEES", {
            "cash": {
                "address": "0xc0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0",
                "tenths_bps": 50,
                "max_fee_rate": "0.001%",
            },
        })
        mgr = _make_order_manager()
        mgr.exchange.approve_builder_fee.return_value = {"status": "ok"}
        with caplog.at_level("WARNING"):
            mgr.approve_configured_builders()
        msg = caplog.text
        assert "exceeds pre-approved max_fee_rate" in msg
        assert "cash" in msg
        # The reported per-order percent must reflect the real fee
        # (tenths_bps=50 → 5 bp → 0.05%).  The previously buggy divisor
        # would print "0.0050%" and silently drift back if reintroduced.
        assert "0.0500%" in msg

    def test_warns_at_default_f_with_tight_cap(self, monkeypatch, caplog):
        """The boundary case the divisor bug would silently miss:
        tenths_bps=10 (1 bp) with max_fee_rate=0.001% (0.1 bp).  The
        exchange rejects every such order; the validator must warn."""
        monkeypatch.setattr(Config, "BUILDER_FEES", {
            "cash": {
                "address": "0xc0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0",
                "tenths_bps": 10,
                "max_fee_rate": "0.001%",
            },
        })
        mgr = _make_order_manager()
        mgr.exchange.approve_builder_fee.return_value = {"status": "ok"}
        with caplog.at_level("WARNING"):
            mgr.approve_configured_builders()
        assert "exceeds pre-approved max_fee_rate" in caplog.text

    def test_no_warning_when_config_is_consistent(self, two_builders, caplog):
        """tenths_bps=10 (1 bp) with max_fee_rate=0.05% (5 bp) leaves
        plenty of headroom — no validator warning expected."""
        mgr = _make_order_manager()
        mgr.exchange.approve_builder_fee.return_value = {"status": "ok"}
        with caplog.at_level("WARNING"):
            mgr.approve_configured_builders()
        assert "exceeds pre-approved max_fee_rate" not in caplog.text
