"""Tests for WebSocket FillFeed — instant fill detection and opposite-side cancel."""

from unittest.mock import MagicMock

from ws.fill_feed import FillFeed


def _make_feed():
    info = MagicMock()
    info.ws_manager = MagicMock()
    info.subscribe.return_value = 42

    tracker = MagicMock()
    feed = FillFeed(info, tracker, "0xabc")
    return feed, info, tracker


class TestFillFeedLifecycle:

    def test_start_subscribes(self):
        feed, info, _ = _make_feed()
        feed.start()

        info.subscribe.assert_called_once()
        sub_arg = info.subscribe.call_args[0][0]
        assert sub_arg["type"] == "userFills"
        assert sub_arg["user"] == "0xabc"
        assert feed.is_running

    def test_stop_unsubscribes(self):
        feed, info, _ = _make_feed()
        feed.start()
        feed.stop()

        info.unsubscribe.assert_called_once()
        assert not feed.is_running

    def test_no_ws_manager_disables(self):
        info = MagicMock()
        info.ws_manager = None
        feed = FillFeed(info, MagicMock(), "0xabc")
        feed.start()

        assert not feed.is_running
        info.subscribe.assert_not_called()


class TestFillCallback:

    def test_live_fill_triggers_cancel(self):
        feed, info, tracker = _make_feed()
        feed.start()

        callback = info.subscribe.call_args[0][1]
        callback({
            "data": {
                "isSnapshot": False,
                "fills": [
                    {"coin": "BTC", "px": "50000", "sz": "0.1", "side": "A"},
                ],
            }
        })

        tracker.cancel_all_orders_for_coin.assert_called_once_with("BTC")
        assert feed.stats["fills"] == 1
        assert feed.stats["cancels"] == 1

    def test_snapshot_skipped(self):
        feed, info, tracker = _make_feed()
        feed.start()

        callback = info.subscribe.call_args[0][1]
        callback({
            "data": {
                "isSnapshot": True,
                "fills": [
                    {"coin": "BTC", "px": "50000", "sz": "0.1", "side": "A"},
                ],
            }
        })

        tracker.cancel_all_orders_for_coin.assert_not_called()
        assert feed.stats["fills"] == 0

    def test_multiple_coins_in_one_message(self):
        feed, info, tracker = _make_feed()
        feed.start()

        callback = info.subscribe.call_args[0][1]
        callback({
            "data": {
                "isSnapshot": False,
                "fills": [
                    {"coin": "BTC", "px": "50000", "sz": "0.1", "side": "A"},
                    {"coin": "ETH", "px": "3000", "sz": "1.0", "side": "B"},
                    {"coin": "BTC", "px": "50001", "sz": "0.1", "side": "A"},
                ],
            }
        })

        # BTC and ETH each cancelled once (deduplicated)
        assert tracker.cancel_all_orders_for_coin.call_count == 2
        cancelled_coins = {
            call.args[0] for call in tracker.cancel_all_orders_for_coin.call_args_list
        }
        assert cancelled_coins == {"BTC", "ETH"}
        assert feed.stats["fills"] == 3  # 3 individual fills
        assert feed.stats["cancels"] == 2  # 2 unique coins

    def test_empty_fills_ignored(self):
        feed, info, tracker = _make_feed()
        feed.start()

        callback = info.subscribe.call_args[0][1]
        callback({"data": {"isSnapshot": False, "fills": []}})

        tracker.cancel_all_orders_for_coin.assert_not_called()

    def test_callback_error_handled(self):
        feed, info, tracker = _make_feed()
        feed.start()

        tracker.cancel_all_orders_for_coin.side_effect = Exception("cancel failed")

        callback = info.subscribe.call_args[0][1]
        callback({
            "data": {
                "isSnapshot": False,
                "fills": [{"coin": "BTC", "px": "50000", "sz": "0.1", "side": "A"}],
            }
        })

        assert feed.stats["errors"] == 1

    def test_stopped_feed_ignores_callbacks(self):
        feed, info, tracker = _make_feed()
        feed.start()
        feed.stop()

        callback = info.subscribe.call_args[0][1]
        callback({
            "data": {
                "isSnapshot": False,
                "fills": [{"coin": "BTC", "px": "50000", "sz": "0.1", "side": "A"}],
            }
        })

        tracker.cancel_all_orders_for_coin.assert_not_called()
