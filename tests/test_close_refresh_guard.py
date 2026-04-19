"""Tests for CloseRefreshGuard — BBO-driven close order refresh."""

import time
from unittest.mock import MagicMock, patch

from ws.close_refresh_guard import CloseRefreshGuard


def _make_levels(bid: float, ask: float):
    """Build l2Book levels from bid/ask prices."""
    return [
        [{"px": str(bid), "sz": "10", "n": 1}],
        [{"px": str(ask), "sz": "10", "n": 1}],
    ]


class TestCloseRefreshGuard:
    """Core guard behavior tests."""

    def test_first_update_establishes_baseline(self):
        closer = MagicMock()
        guard = CloseRefreshGuard(closer, threshold_bps=1.0)

        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))

        closer.invalidate_close_order.assert_not_called()
        assert guard.stats["refreshes_triggered"] == 0

    def test_small_change_no_refresh(self):
        closer = MagicMock()
        guard = CloseRefreshGuard(closer, threshold_bps=2.0)

        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))
        # 1 bps change — below 2 bps threshold
        guard.on_l2_update("SP500", _make_levels(100.01, 100.11))

        closer.invalidate_close_order.assert_not_called()

    def test_large_change_triggers_refresh(self):
        closer = MagicMock()
        closer.invalidate_close_order.return_value = True
        guard = CloseRefreshGuard(closer, threshold_bps=1.0)

        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))
        # ~3 bps change — above 1 bps threshold
        guard.on_l2_update("SP500", _make_levels(100.03, 100.13))

        closer.invalidate_close_order.assert_called_once_with("SP500")
        assert guard.stats["refreshes_triggered"] == 1

    def test_no_close_order_increments_skipped(self):
        closer = MagicMock()
        closer.invalidate_close_order.return_value = False
        guard = CloseRefreshGuard(closer, threshold_bps=1.0)

        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))
        guard.on_l2_update("SP500", _make_levels(100.05, 100.15))

        closer.invalidate_close_order.assert_called_once_with("SP500")
        assert guard.stats["skipped_no_order"] == 1
        assert guard.stats["refreshes_triggered"] == 0

    @patch('ws.close_refresh_guard.time')
    def test_rate_limiting(self, mock_time):
        closer = MagicMock()
        closer.invalidate_close_order.return_value = True
        guard = CloseRefreshGuard(closer, threshold_bps=1.0, min_refresh_interval=3.0)

        mock_time.monotonic.return_value = 100.0
        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))
        # First trigger — no rate limit (first call for this coin)
        guard.on_l2_update("SP500", _make_levels(100.05, 100.15))
        assert closer.invalidate_close_order.call_count == 1

        # Within rate limit window (1s later)
        mock_time.monotonic.return_value = 101.0
        guard.on_l2_update("SP500", _make_levels(100.10, 100.20))
        assert closer.invalidate_close_order.call_count == 1  # Blocked by rate limit

        # After rate limit window (4s after first trigger)
        mock_time.monotonic.return_value = 104.0
        guard.on_l2_update("SP500", _make_levels(100.15, 100.25))
        assert closer.invalidate_close_order.call_count == 2

    def test_independent_coins(self):
        closer = MagicMock()
        closer.invalidate_close_order.return_value = True
        guard = CloseRefreshGuard(closer, threshold_bps=1.0)

        # Establish baselines
        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))
        guard.on_l2_update("NVDA", _make_levels(200.0, 200.2))

        # Change SP500 only
        guard.on_l2_update("SP500", _make_levels(100.05, 100.15))

        closer.invalidate_close_order.assert_called_once_with("SP500")

    def test_stop_prevents_further_processing(self):
        closer = MagicMock()
        guard = CloseRefreshGuard(closer, threshold_bps=1.0)

        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))
        guard.stop()

        guard.on_l2_update("SP500", _make_levels(100.10, 100.20))
        closer.invalidate_close_order.assert_not_called()
        assert guard.is_running is False

    def test_invalid_levels_no_crash(self):
        closer = MagicMock()
        guard = CloseRefreshGuard(closer, threshold_bps=1.0)

        # Empty levels
        guard.on_l2_update("SP500", [[], []])
        assert guard.stats["errors"] == 0

        # bid=0
        guard.on_l2_update("SP500", _make_levels(0, 100.1))
        assert guard.stats["errors"] == 0

    def test_error_counting(self):
        closer = MagicMock()
        guard = CloseRefreshGuard(closer, threshold_bps=1.0)

        # Pass malformed levels that will cause an exception
        guard.on_l2_update("SP500", "not-a-list")
        assert guard.stats["errors"] == 1


class TestInvalidateCloseOrder:
    """Tests for PositionCloser.invalidate_close_order()."""

    def test_invalidate_with_close_order(self):
        from strategies.mm_position_closer import PositionCloser

        om = MagicMock()
        md = MagicMock()
        closer = PositionCloser(
            order_manager=om, market_data=md,
            spread_bps=10, max_position_age_seconds=120,
            maker_only=True, taker_fallback_age_seconds=120,
        )

        # Simulate a tracked position with a close order
        entry_time = time.monotonic()
        closer._open_positions["SP500"] = (entry_time, 12345, 0)

        result = closer.invalidate_close_order("SP500")

        assert result is True
        om.cancel_order.assert_called_once_with(12345, "SP500")
        # OID should be cleared, tier and entry_time preserved
        assert closer._open_positions["SP500"][0] == entry_time
        assert closer._open_positions["SP500"][1] is None
        assert closer._open_positions["SP500"][2] == 0

    def test_invalidate_no_position(self):
        from strategies.mm_position_closer import PositionCloser

        om = MagicMock()
        md = MagicMock()
        closer = PositionCloser(
            order_manager=om, market_data=md,
            spread_bps=10, max_position_age_seconds=120,
            maker_only=True, taker_fallback_age_seconds=120,
        )

        result = closer.invalidate_close_order("SP500")

        assert result is False
        om.cancel_order.assert_not_called()

    def test_invalidate_no_close_oid(self):
        from strategies.mm_position_closer import PositionCloser

        om = MagicMock()
        md = MagicMock()
        closer = PositionCloser(
            order_manager=om, market_data=md,
            spread_bps=10, max_position_age_seconds=120,
            maker_only=True, taker_fallback_age_seconds=120,
        )

        # Position tracked but no close order yet
        closer._open_positions["SP500"] = (time.monotonic(), None, 0)

        result = closer.invalidate_close_order("SP500")

        assert result is False
        om.cancel_order.assert_not_called()

    def test_invalidate_cancel_fails_still_clears_oid(self):
        from strategies.mm_position_closer import PositionCloser
        om = MagicMock()
        om.cancel_order.side_effect = Exception("API error")
        md = MagicMock()
        closer = PositionCloser(
            order_manager=om, market_data=md,
            spread_bps=10, max_position_age_seconds=120,
            maker_only=True, taker_fallback_age_seconds=120,
        )

        closer._open_positions["SP500"] = (time.monotonic(), 99999, 1)

        result = closer.invalidate_close_order("SP500")

        assert result is True
        # OID should still be cleared even if cancel failed
        assert closer._open_positions["SP500"][1] is None

    def test_invalidate_preserves_tier(self):
        from strategies.mm_position_closer import PositionCloser

        om = MagicMock()
        md = MagicMock()
        closer = PositionCloser(
            order_manager=om, market_data=md,
            spread_bps=10, max_position_age_seconds=120,
            maker_only=True, taker_fallback_age_seconds=120,
        )

        closer._open_positions["SP500"] = (time.monotonic(), 12345, 2)  # tier=AGGRESSIVE

        closer.invalidate_close_order("SP500")

        assert closer._open_positions["SP500"][2] == 2  # Tier preserved
