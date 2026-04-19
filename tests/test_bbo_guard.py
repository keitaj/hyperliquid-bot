"""Tests for BboGuard — BBO change detection and stale quote cancellation."""

from unittest.mock import MagicMock, patch

from ws.bbo_guard import BboGuard


def _make_levels(bid: float, ask: float):
    """Build an l2Book-style levels list from a single bid/ask."""
    return [
        [{"px": str(bid), "sz": "10.0", "n": 3}],
        [{"px": str(ask), "sz": "8.0", "n": 2}],
    ]


def _make_guard(threshold_bps: float = 2.0, min_cancel_interval: float = 0.0):
    tracker = MagicMock()
    guard = BboGuard(tracker, threshold_bps=threshold_bps, min_cancel_interval=min_cancel_interval)
    return guard, tracker


class TestBboGuardBasics:

    def test_first_update_sets_baseline_no_cancel(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", _make_levels(100.00, 100.02))

        tracker.cancel_all_orders_for_coin.assert_not_called()
        assert guard.stats["changes_detected"] == 0

    def test_small_change_no_cancel(self):
        guard, tracker = _make_guard(threshold_bps=2.0)
        guard.on_l2_update("BTC", _make_levels(100.00, 100.02))
        # 1 bps change — below threshold
        guard.on_l2_update("BTC", _make_levels(100.01, 100.03))

        tracker.cancel_all_orders_for_coin.assert_not_called()
        assert guard.stats["changes_detected"] == 0

    def test_large_change_triggers_cancel(self):
        guard, tracker = _make_guard(threshold_bps=2.0)
        guard.on_l2_update("BTC", _make_levels(100.00, 100.02))
        # 3 bps change on bid — above threshold
        guard.on_l2_update("BTC", _make_levels(100.03, 100.05))

        tracker.cancel_all_orders_for_coin.assert_called_once_with("BTC")
        assert guard.stats["changes_detected"] == 1
        assert guard.stats["cancels_triggered"] == 1

    def test_ask_change_triggers_cancel(self):
        guard, tracker = _make_guard(threshold_bps=2.0)
        guard.on_l2_update("BTC", _make_levels(100.00, 100.02))
        # Ask drops by 3 bps
        guard.on_l2_update("BTC", _make_levels(100.00, 99.99))

        tracker.cancel_all_orders_for_coin.assert_called_once_with("BTC")

    def test_multiple_coins_independent(self):
        guard, tracker = _make_guard(threshold_bps=2.0)
        guard.on_l2_update("BTC", _make_levels(100.00, 100.02))
        guard.on_l2_update("ETH", _make_levels(3000.0, 3000.6))

        # BTC change triggers cancel, ETH stays within threshold
        guard.on_l2_update("BTC", _make_levels(100.05, 100.07))
        guard.on_l2_update("ETH", _make_levels(3000.03, 3000.63))

        tracker.cancel_all_orders_for_coin.assert_called_once_with("BTC")


class TestBboGuardEdgeCases:

    def test_zero_bid_skipped(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", [[{"px": "0", "sz": "1"}], [{"px": "100", "sz": "1"}]])

        tracker.cancel_all_orders_for_coin.assert_not_called()
        assert guard.stats["errors"] == 0

    def test_zero_ask_skipped(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", [[{"px": "100", "sz": "1"}], [{"px": "0", "sz": "1"}]])

        tracker.cancel_all_orders_for_coin.assert_not_called()

    def test_empty_bids_skipped(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", [[], [{"px": "100", "sz": "1"}]])

        tracker.cancel_all_orders_for_coin.assert_not_called()
        assert guard.stats["errors"] == 0

    def test_empty_asks_skipped(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", [[{"px": "100", "sz": "1"}], []])

        tracker.cancel_all_orders_for_coin.assert_not_called()

    def test_stopped_guard_ignores_updates(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", _make_levels(100.00, 100.02))
        guard.stop()

        guard.on_l2_update("BTC", _make_levels(100.10, 100.12))  # 10 bps change

        tracker.cancel_all_orders_for_coin.assert_not_called()
        assert not guard.is_running

    def test_callback_error_increments_counter(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("BTC", _make_levels(100.00, 100.02))

        tracker.cancel_all_orders_for_coin.side_effect = Exception("API error")
        guard.on_l2_update("BTC", _make_levels(100.05, 100.07))

        assert guard.stats["errors"] == 1


class TestBboGuardRateLimit:

    def test_min_cancel_interval_respected(self):
        guard, tracker = _make_guard(threshold_bps=2.0, min_cancel_interval=10.0)

        guard.on_l2_update("BTC", _make_levels(100.00, 100.02))

        # First change — should cancel
        with patch("ws.bbo_guard.time") as mock_time:
            mock_time.monotonic.return_value = 1000.0
            guard.on_l2_update("BTC", _make_levels(100.05, 100.07))
            assert tracker.cancel_all_orders_for_coin.call_count == 1

            # Second change 5s later — should be rate-limited (interval=10s)
            mock_time.monotonic.return_value = 1005.0
            guard.on_l2_update("BTC", _make_levels(100.10, 100.12))
            assert tracker.cancel_all_orders_for_coin.call_count == 1

            # Third change 11s later — should cancel again
            mock_time.monotonic.return_value = 1011.0
            guard.on_l2_update("BTC", _make_levels(100.15, 100.17))
            assert tracker.cancel_all_orders_for_coin.call_count == 2

        assert guard.stats["changes_detected"] == 3
        assert guard.stats["cancels_triggered"] == 2


class TestMarketDataFeedListener:
    """Test that MarketDataFeed listener mechanism works correctly."""

    def test_add_listener_called_on_update(self):
        from ws.market_data_feed import MarketDataFeed

        info = MagicMock()
        info.ws_manager = MagicMock()
        market_data = MagicMock()

        feed = MarketDataFeed(info, market_data, ["BTC"])
        feed.start()

        listener = MagicMock()
        feed.add_listener(listener)

        # Simulate WS callback
        callback = info.subscribe.call_args[0][1]
        levels = _make_levels(100.0, 100.02)
        callback({"data": {"coin": "BTC", "levels": levels}})

        listener.assert_called_once_with("BTC", levels)

    def test_listener_error_does_not_break_feed(self):
        from ws.market_data_feed import MarketDataFeed

        info = MagicMock()
        info.ws_manager = MagicMock()
        market_data = MagicMock()

        feed = MarketDataFeed(info, market_data, ["BTC"])
        feed.start()

        bad_listener = MagicMock(side_effect=Exception("boom"))
        good_listener = MagicMock()
        feed.add_listener(bad_listener)
        feed.add_listener(good_listener)

        callback = info.subscribe.call_args[0][1]
        levels = _make_levels(100.0, 100.02)
        callback({"data": {"coin": "BTC", "levels": levels}})

        # Good listener still gets called despite bad listener failing
        good_listener.assert_called_once_with("BTC", levels)
        # Cache still updated
        market_data.update_from_ws.assert_called_once()
