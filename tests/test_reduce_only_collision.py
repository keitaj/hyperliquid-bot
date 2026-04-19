"""Tests for reduce-only collision prevention.

Three-layer defense against 'Reduce only order would increase position' errors:
1. FillFeed → PositionCloser notification on fill
2. manage() defers re-placement when close OID disappears
3. _place_take_profit() verifies position before placing reduce_only
"""

import time
from unittest.mock import MagicMock

from strategies.mm_position_closer import PositionCloser
from ws.fill_feed import FillFeed


def _make_closer(**kwargs):
    """Create a PositionCloser with mocked dependencies."""
    om = MagicMock()
    md = MagicMock()
    defaults = dict(
        order_manager=om, market_data=md,
        spread_bps=10, max_position_age_seconds=120,
        maker_only=True, taker_fallback_age_seconds=120,
    )
    defaults.update(kwargs)
    closer = PositionCloser(**defaults)
    return closer, om, md


# ------------------------------------------------------------------ #
#  Fix 1: FillFeed → PositionCloser notification
# ------------------------------------------------------------------ #

class TestFillFeedPositionCloserNotification:
    """FillFeed notifies PositionCloser when fills are detected."""

    def test_fill_clears_position_tracking(self):
        info = MagicMock()
        order_tracker = MagicMock()
        closer_mock = MagicMock()

        feed = FillFeed(info, order_tracker, "0x123")
        feed._running = True
        feed.set_position_closer(closer_mock)

        msg = {
            "data": {
                "isSnapshot": False,
                "fills": [{"coin": "SP500", "px": "100", "sz": "0.1", "side": "B"}],
            }
        }
        feed._on_fill(msg)

        closer_mock.on_position_closed.assert_called_once_with("SP500")

    def test_multiple_coins_all_notified(self):
        info = MagicMock()
        order_tracker = MagicMock()
        closer_mock = MagicMock()

        feed = FillFeed(info, order_tracker, "0x123")
        feed._running = True
        feed.set_position_closer(closer_mock)

        msg = {
            "data": {
                "isSnapshot": False,
                "fills": [
                    {"coin": "SP500", "px": "100", "sz": "0.1", "side": "B"},
                    {"coin": "NVDA", "px": "200", "sz": "0.1", "side": "A"},
                ],
            }
        }
        feed._on_fill(msg)

        assert closer_mock.on_position_closed.call_count == 2

    def test_no_closer_no_error(self):
        info = MagicMock()
        order_tracker = MagicMock()

        feed = FillFeed(info, order_tracker, "0x123")
        feed._running = True

        msg = {
            "data": {
                "isSnapshot": False,
                "fills": [{"coin": "SP500", "px": "100", "sz": "0.1", "side": "B"}],
            }
        }
        feed._on_fill(msg)  # Should not raise

    def test_snapshot_does_not_notify(self):
        info = MagicMock()
        order_tracker = MagicMock()
        closer_mock = MagicMock()

        feed = FillFeed(info, order_tracker, "0x123")
        feed._running = True
        feed.set_position_closer(closer_mock)

        msg = {
            "data": {
                "isSnapshot": True,
                "fills": [{"coin": "SP500", "px": "100", "sz": "0.1", "side": "B"}],
            }
        }
        feed._on_fill(msg)

        closer_mock.on_position_closed.assert_not_called()


# ------------------------------------------------------------------ #
#  Fix 2: manage() defers re-placement on dead OID
# ------------------------------------------------------------------ #

class TestManageDeadOidDefer:
    """manage() returns early when close OID disappears."""

    def test_dead_oid_defers_placement(self):
        closer, om, md = _make_closer()

        entry_time = time.monotonic()
        closer._open_positions["SP500"] = (entry_time, 12345, 0)

        om.get_open_orders.return_value = []  # OID 12345 not alive

        position = {'size': 0.5, 'entry_price': 100.0}
        closer.manage("SP500", position, MagicMock())

        # Should NOT have placed a new close order (deferred)
        om.create_limit_order.assert_not_called()
        assert closer._open_positions["SP500"][1] is None

    def test_dead_oid_next_cycle_places_order(self):
        closer, om, md = _make_closer()

        entry_time = time.monotonic()
        closer._open_positions["SP500"] = (entry_time, None, 0)

        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.9
        market_data.ask = 100.1
        md.get_market_data.return_value = market_data
        md.price_rounding_params.return_value = (4, 0.01)
        md.round_size.return_value = 0.1

        om.get_all_positions.return_value = [{'coin': 'SP500', 'szi': '0.5'}]

        order_result = MagicMock()
        order_result.id = 99999
        om.create_limit_order.return_value = order_result

        position = {'size': 0.5, 'entry_price': 100.0}
        closer.manage("SP500", position, MagicMock())

        om.create_limit_order.assert_called_once()

    def test_max_age_overrides_defer(self):
        closer, om, md = _make_closer(max_position_age_seconds=10, taker_fallback_age_seconds=10)

        entry_time = time.monotonic() - 25
        closer._open_positions["SP500"] = (entry_time, 12345, 0)

        position = {'size': 0.5, 'entry_price': 100.0}
        close_fn = MagicMock()
        closer.manage("SP500", position, close_fn)

        close_fn.assert_called_once_with("SP500")


# ------------------------------------------------------------------ #
#  Fix 3: _place_take_profit() position verification
# ------------------------------------------------------------------ #

class TestPlaceTakeProfitPositionCheck:
    """_place_take_profit() verifies position before placing reduce_only."""

    def test_position_closed_skips_order(self):
        closer, om, md = _make_closer()

        entry_time = time.monotonic()
        closer._open_positions["SP500"] = (entry_time, None, 0)

        om.get_all_positions.return_value = [{'coin': 'SP500', 'szi': '0'}]

        result = closer._place_take_profit("SP500", 0.5, 100.0, entry_time, 0)

        assert result is False
        om.create_limit_order.assert_not_called()
        assert "SP500" not in closer._open_positions

    def test_position_missing_skips_order(self):
        closer, om, md = _make_closer()

        entry_time = time.monotonic()
        closer._open_positions["SP500"] = (entry_time, None, 0)

        om.get_all_positions.return_value = [{'coin': 'NVDA', 'szi': '0.5'}]

        result = closer._place_take_profit("SP500", 0.5, 100.0, entry_time, 0)

        assert result is False
        om.create_limit_order.assert_not_called()

    def test_position_exists_proceeds(self):
        closer, om, md = _make_closer()

        entry_time = time.monotonic()
        closer._open_positions["SP500"] = (entry_time, None, 0)

        om.get_all_positions.return_value = [{'coin': 'SP500', 'szi': '0.5'}]

        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.9
        market_data.ask = 100.1
        md.get_market_data.return_value = market_data
        md.price_rounding_params.return_value = (4, 0.01)
        md.round_size.return_value = 0.1

        order_result = MagicMock()
        order_result.id = 12345
        om.create_limit_order.return_value = order_result

        result = closer._place_take_profit("SP500", 0.5, 100.0, entry_time, 0)

        assert result is True
        om.create_limit_order.assert_called_once()

    def test_api_error_proceeds_with_placement(self):
        closer, om, md = _make_closer()

        entry_time = time.monotonic()
        closer._open_positions["SP500"] = (entry_time, None, 0)

        om.get_all_positions.side_effect = Exception("API timeout")

        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.9
        market_data.ask = 100.1
        md.get_market_data.return_value = market_data
        md.price_rounding_params.return_value = (4, 0.01)
        md.round_size.return_value = 0.1

        order_result = MagicMock()
        order_result.id = 12345
        om.create_limit_order.return_value = order_result

        result = closer._place_take_profit("SP500", 0.5, 100.0, entry_time, 0)

        assert result is True
        om.create_limit_order.assert_called_once()


# ------------------------------------------------------------------ #
#  Integration
# ------------------------------------------------------------------ #

class TestIntegration:
    """End-to-end scenarios."""

    def test_close_fill_clears_tracking(self):
        closer, om, md = _make_closer()

        entry_time = time.monotonic()
        closer._open_positions["SP500"] = (entry_time, 12345, 0)

        closer.on_position_closed("SP500")

        assert "SP500" not in closer._open_positions

    def test_on_position_closed_idempotent(self):
        closer, om, md = _make_closer()

        closer.on_position_closed("SP500")  # Not tracked — should not raise
        closer.on_position_closed("SP500")  # Idempotent
