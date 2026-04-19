"""Tests for ImbalanceGuard — L2 book imbalance detection and one-sided cancel."""

from unittest.mock import MagicMock, patch

from ws.imbalance_guard import ImbalanceGuard


def _make_levels(bid_sizes, ask_sizes, bid_px=100.0, ask_px=100.02):
    """Build l2Book-style levels with controllable sizes.

    Each side gets one entry per size value in the list.
    """
    bids = [{"px": str(bid_px - i * 0.01), "sz": str(s)} for i, s in enumerate(bid_sizes)]
    asks = [{"px": str(ask_px + i * 0.01), "sz": str(s)} for i, s in enumerate(ask_sizes)]
    return [bids, asks]


def _make_guard(threshold: float = 0.5, depth: int = 5, min_cancel_interval: float = 0.0):
    tracker = MagicMock()
    guard = ImbalanceGuard(tracker, threshold=threshold, depth=depth,
                           min_cancel_interval=min_cancel_interval)
    return guard, tracker


class TestImbalanceGuardBasics:

    def test_neutral_no_cancel(self):
        """Balanced book → no cancel."""
        guard, tracker = _make_guard()
        # Equal bid/ask sizes → imbalance = 0
        guard.on_l2_update("BTC", _make_levels([10, 10], [10, 10]))

        tracker.cancel_orders_by_side.assert_not_called()

    def test_ask_heavy_cancels_buy(self):
        """Ask-heavy book → cancel BUY orders."""
        guard, tracker = _make_guard(threshold=0.5)
        # Baseline: neutral
        guard.on_l2_update("BTC", _make_levels([10, 10], [10, 10]))
        # Shift to ask-heavy: bid=5, ask=20 → imbalance = (5-20)/25 = -0.6
        guard.on_l2_update("BTC", _make_levels([5], [20]))

        tracker.cancel_orders_by_side.assert_called_once_with("BTC", "B")
        assert guard.stats["changes_detected"] == 1
        assert guard.stats["cancels_triggered"] == 1

    def test_bid_heavy_cancels_sell(self):
        """Bid-heavy book → cancel SELL orders."""
        guard, tracker = _make_guard(threshold=0.5)
        guard.on_l2_update("BTC", _make_levels([10, 10], [10, 10]))
        # bid=20, ask=5 → imbalance = (20-5)/25 = +0.6
        guard.on_l2_update("BTC", _make_levels([20], [5]))

        tracker.cancel_orders_by_side.assert_called_once_with("BTC", "A")

    def test_below_threshold_no_cancel(self):
        """Moderate imbalance below threshold → no cancel."""
        guard, tracker = _make_guard(threshold=0.5)
        guard.on_l2_update("BTC", _make_levels([10], [10]))
        # imbalance = (12-8)/20 = 0.2, below 0.5 threshold
        guard.on_l2_update("BTC", _make_levels([12], [8]))

        tracker.cancel_orders_by_side.assert_not_called()

    def test_multiple_coins_independent(self):
        """Each coin has independent state."""
        guard, tracker = _make_guard(threshold=0.5)
        guard.on_l2_update("BTC", _make_levels([10], [10]))
        guard.on_l2_update("ETH", _make_levels([10], [10]))

        # BTC goes ask-heavy, ETH stays neutral
        guard.on_l2_update("BTC", _make_levels([2], [18]))
        guard.on_l2_update("ETH", _make_levels([11], [9]))

        tracker.cancel_orders_by_side.assert_called_once_with("BTC", "B")


class TestImbalanceGuardStateTransitions:

    def test_same_state_no_recancel(self):
        """Staying in buy_risky → no repeated cancel."""
        guard, tracker = _make_guard(threshold=0.5)
        guard.on_l2_update("BTC", _make_levels([10], [10]))
        guard.on_l2_update("BTC", _make_levels([2], [18]))  # → buy_risky
        guard.on_l2_update("BTC", _make_levels([1], [19]))  # still buy_risky

        assert tracker.cancel_orders_by_side.call_count == 1

    def test_neutral_then_risky_again_recancels(self):
        """buy_risky → neutral → buy_risky fires again."""
        guard, tracker = _make_guard(threshold=0.5)
        guard.on_l2_update("BTC", _make_levels([10], [10]))  # neutral
        guard.on_l2_update("BTC", _make_levels([2], [18]))   # → buy_risky (cancel)
        guard.on_l2_update("BTC", _make_levels([10], [10]))  # → neutral
        guard.on_l2_update("BTC", _make_levels([2], [18]))   # → buy_risky again (cancel)

        assert tracker.cancel_orders_by_side.call_count == 2
        assert guard.stats["changes_detected"] == 2

    def test_direction_switch(self):
        """buy_risky → sell_risky cancels sell side."""
        guard, tracker = _make_guard(threshold=0.5)
        guard.on_l2_update("BTC", _make_levels([10], [10]))
        guard.on_l2_update("BTC", _make_levels([2], [18]))   # → buy_risky, cancel B
        guard.on_l2_update("BTC", _make_levels([18], [2]))   # → sell_risky, cancel A

        assert tracker.cancel_orders_by_side.call_count == 2
        calls = tracker.cancel_orders_by_side.call_args_list
        assert calls[0].args == ("BTC", "B")
        assert calls[1].args == ("BTC", "A")

    def test_return_to_neutral_no_cancel(self):
        """Transitioning to neutral never triggers a cancel."""
        guard, tracker = _make_guard(threshold=0.5)
        guard.on_l2_update("BTC", _make_levels([2], [18]))   # → buy_risky (from neutral)
        guard.on_l2_update("BTC", _make_levels([10], [10]))  # → neutral

        # Only 1 cancel (the initial transition)
        assert tracker.cancel_orders_by_side.call_count == 1

    def test_first_update_risky_triggers_cancel(self):
        """If the very first update is already risky, cancel fires."""
        guard, tracker = _make_guard(threshold=0.5)
        guard.on_l2_update("BTC", _make_levels([2], [18]))

        tracker.cancel_orders_by_side.assert_called_once_with("BTC", "B")


class TestImbalanceGuardEdgeCases:

    def test_empty_bids_skipped(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", [[], [{"px": "100", "sz": "10"}]])

        tracker.cancel_orders_by_side.assert_not_called()
        assert guard.stats["errors"] == 0

    def test_empty_asks_skipped(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", [[{"px": "100", "sz": "10"}], []])

        tracker.cancel_orders_by_side.assert_not_called()

    def test_short_levels_skipped(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", [[{"px": "100", "sz": "10"}]])

        tracker.cancel_orders_by_side.assert_not_called()

    def test_stopped_guard_ignores_updates(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", _make_levels([10], [10]))
        guard.stop()

        guard.on_l2_update("BTC", _make_levels([2], [18]))
        tracker.cancel_orders_by_side.assert_not_called()
        assert not guard.is_running

    def test_callback_error_increments_counter(self):
        guard, tracker = _make_guard(threshold=0.5)
        guard.on_l2_update("BTC", _make_levels([10], [10]))
        tracker.cancel_orders_by_side.side_effect = Exception("API error")

        guard.on_l2_update("BTC", _make_levels([2], [18]))
        assert guard.stats["errors"] == 1

    def test_depth_parameter_limits_levels(self):
        """Guard should use at most `depth` levels."""
        guard, tracker = _make_guard(threshold=0.5, depth=2)
        # 5 levels each, but only top 2 used:
        # top 2 bids: 2+2=4, top 2 asks: 18+18=36 → imb = (4-36)/40 = -0.8
        guard.on_l2_update("BTC", _make_levels([10] * 5, [10] * 5))
        guard.on_l2_update("BTC", _make_levels([2, 2, 100, 100, 100], [18, 18, 1, 1, 1]))

        tracker.cancel_orders_by_side.assert_called_once_with("BTC", "B")


class TestImbalanceGuardRateLimit:

    def test_min_cancel_interval_respected(self):
        guard, tracker = _make_guard(threshold=0.5, min_cancel_interval=10.0)

        guard.on_l2_update("BTC", _make_levels([10], [10]))

        with patch("ws.imbalance_guard.time") as mock_time:
            mock_time.monotonic.return_value = 1000.0
            guard.on_l2_update("BTC", _make_levels([2], [18]))   # cancel
            assert tracker.cancel_orders_by_side.call_count == 1

            # Return to neutral and back — within interval
            mock_time.monotonic.return_value = 1005.0
            guard.on_l2_update("BTC", _make_levels([10], [10]))  # → neutral
            guard.on_l2_update("BTC", _make_levels([2], [18]))   # → buy_risky, rate-limited
            assert tracker.cancel_orders_by_side.call_count == 1

            # After interval expires
            mock_time.monotonic.return_value = 1011.0
            guard.on_l2_update("BTC", _make_levels([10], [10]))  # → neutral
            guard.on_l2_update("BTC", _make_levels([2], [18]))   # → buy_risky, fires
            assert tracker.cancel_orders_by_side.call_count == 2

        assert guard.stats["changes_detected"] == 3
        assert guard.stats["cancels_triggered"] == 2


class TestOrderTrackerCancelBySide:
    """Test cancel_orders_by_side on the real OrderTracker."""

    def test_cancels_only_matching_side(self):
        from strategies.mm_order_tracker import OrderTracker

        om = MagicMock()
        om.bulk_cancel_orders.return_value = 1
        tracker = OrderTracker(om, refresh_interval_seconds=30, max_open_orders=4)

        tracker.record_order("BTC", 100, "B")
        tracker.record_order("BTC", 101, "A")
        tracker.record_order("BTC", 102, "B")

        tracker.cancel_orders_by_side("BTC", "B")

        # Only buy orders cancelled
        cancel_requests = om.bulk_cancel_orders.call_args[0][0]
        cancelled_oids = {r["oid"] for r in cancel_requests}
        assert cancelled_oids == {100, 102}

        # Sell order remains tracked
        assert tracker.get_order_count("BTC") == 1

    def test_no_orders_for_side_is_noop(self):
        from strategies.mm_order_tracker import OrderTracker

        om = MagicMock()
        tracker = OrderTracker(om, refresh_interval_seconds=30, max_open_orders=4)

        tracker.record_order("BTC", 100, "B")
        tracker.cancel_orders_by_side("BTC", "A")

        om.bulk_cancel_orders.assert_not_called()
        assert tracker.get_order_count("BTC") == 1

    def test_empty_coin_is_noop(self):
        from strategies.mm_order_tracker import OrderTracker

        om = MagicMock()
        tracker = OrderTracker(om, refresh_interval_seconds=30, max_open_orders=4)

        tracker.cancel_orders_by_side("BTC", "B")
        om.bulk_cancel_orders.assert_not_called()
