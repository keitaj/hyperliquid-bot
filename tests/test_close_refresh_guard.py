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

    @patch('ws.close_refresh_guard.time')
    def test_skip_does_not_update_rate_limit(self, mock_time):
        """When close_oid is None (skip), rate limit timer should NOT be updated.

        This ensures that the next BBO change after close order placement
        can immediately trigger a refresh.
        """
        closer = MagicMock()
        guard = CloseRefreshGuard(closer, threshold_bps=1.0, min_refresh_interval=3.0)

        mock_time.monotonic.return_value = 100.0
        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))

        # First BBO change: close_oid=None → skip, should NOT set rate limit
        closer.invalidate_close_order.return_value = False
        mock_time.monotonic.return_value = 100.5
        guard.on_l2_update("SP500", _make_levels(100.05, 100.15))
        assert guard.stats["skipped_no_order"] == 1
        assert "SP500" not in guard._last_refresh_time

        # Second BBO change at t=101 (within 3s of skip): close_oid now exists
        # Should NOT be rate-limited because the skip didn't set the timer
        closer.invalidate_close_order.return_value = True
        mock_time.monotonic.return_value = 101.0
        guard.on_l2_update("SP500", _make_levels(100.10, 100.20))
        assert guard.stats["refreshes_triggered"] == 1
        assert guard._last_refresh_time["SP500"] == 101.0

    @patch('ws.close_refresh_guard.time')
    def test_refresh_sets_rate_limit(self, mock_time):
        """After a successful refresh, subsequent calls within interval are blocked."""
        closer = MagicMock()
        closer.invalidate_close_order.return_value = True
        guard = CloseRefreshGuard(closer, threshold_bps=1.0, min_refresh_interval=3.0)

        mock_time.monotonic.return_value = 100.0
        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))

        # First refresh succeeds
        mock_time.monotonic.return_value = 101.0
        guard.on_l2_update("SP500", _make_levels(100.05, 100.15))
        assert closer.invalidate_close_order.call_count == 1

        # Within rate limit (1s later, < 3s interval)
        mock_time.monotonic.return_value = 102.0
        guard.on_l2_update("SP500", _make_levels(100.10, 100.20))
        assert closer.invalidate_close_order.call_count == 1  # blocked

        # After rate limit expires
        mock_time.monotonic.return_value = 105.0
        guard.on_l2_update("SP500", _make_levels(100.15, 100.25))
        assert closer.invalidate_close_order.call_count == 2

    @patch('ws.close_refresh_guard.time.monotonic')
    def test_periodic_summary_log(self, mock_monotonic):
        """Summary log is emitted after summary_interval."""
        closer = MagicMock()
        closer.invalidate_close_order.return_value = False

        mock_monotonic.return_value = 0.0
        guard = CloseRefreshGuard(
            closer, threshold_bps=1.0, summary_interval=10.0
        )

        guard.on_l2_update("SP500", _make_levels(100.0, 100.1))

        # Trigger within summary interval — no summary yet
        mock_monotonic.return_value = 5.0
        guard.on_l2_update("SP500", _make_levels(100.05, 100.15))
        assert guard._period_skips == 1

        # Trigger after summary interval — should reset counters
        mock_monotonic.return_value = 11.0
        guard.on_l2_update("SP500", _make_levels(100.10, 100.20))
        assert guard._period_skips == 0  # reset after summary
        assert guard._last_summary_time == 11.0


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
