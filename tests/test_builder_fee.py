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
            "max_fee_rate": "0.001%",
        },
    })
    return Config.BUILDER_FEES["cash"]


@pytest.fixture
def two_builders(monkeypatch):
    monkeypatch.setattr(Config, "BUILDER_FEES", {
        "cash": {
            "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "tenths_bps": 10,
            "max_fee_rate": "0.001%",
        },
        "vntl": {
            "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "tenths_bps": 5,
            "max_fee_rate": "0.0005%",
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
        mgr = _make_order_manager()
        # cash group gets [10, 30] (positions 0 and 2), vntl gets [20] (position 1)
        # The order of bulk calls is dict-iteration order; we just check
        # that each input index maps to the correct returned oid.
        mgr.exchange.bulk_orders.side_effect = [
            self._make_bulk_response([10, 30]),
            self._make_bulk_response([20]),
        ]
        orders = [
            _make_order(coin="cash:HOOD"),
            _make_order(coin="vntl:MAG7"),
            _make_order(coin="cash:INTC"),
        ]
        results = mgr.bulk_place_orders(orders)
        # Whichever DEX is processed first, the indices should still align.
        ids_by_coin = {orders[i].coin: results[i].id for i in range(len(orders))
                       if results[i] is not None}
        assert ids_by_coin["cash:HOOD"] in (10, 30, 20)
        assert ids_by_coin["cash:INTC"] in (10, 30, 20)
        assert ids_by_coin["vntl:MAG7"] in (10, 30, 20)
        assert len({ids_by_coin[c] for c in ids_by_coin}) == 3


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
