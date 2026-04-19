"""Tests for WsReconnector — WebSocket auto-reconnect monitor."""

import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from ws.ws_reconnector import WsReconnector


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #


def _make_bot(all_stale=False, ws_thread_alive=True):
    """Create a mock bot with controllable WS state."""
    bot = MagicMock()
    bot.coins = ["BTC", "ETH"]
    bot.account_address = "0xtest"
    bot.api_timeout = 10
    bot.strategy_config = {
        "bbo_guard_threshold_bps": 2.0,
        "imbalance_guard_threshold": 0.5,
        "imbalance_guard_depth": 5,
    }
    bot._build_perp_dexs.return_value = ["xyz"]

    # WS feed mock
    ws_feed = MagicMock()
    ws_feed.coins = ["BTC", "ETH"]
    if all_stale:
        ws_feed.stale_coins.return_value = ["BTC", "ETH"]
    else:
        ws_feed.stale_coins.return_value = []

    # SDK WS manager mock
    ws_mgr = MagicMock()
    ws_mgr.is_alive.return_value = ws_thread_alive
    ws_feed.info.ws_manager = ws_mgr

    bot.ws_feed = ws_feed
    bot.fill_feed = MagicMock()
    bot.bbo_guard = MagicMock()
    bot.imbalance_guard = MagicMock()

    # strategy with order_tracker
    bot.strategy.order_tracker = MagicMock()

    return bot


# ------------------------------------------------------------------ #
#  Tests
# ------------------------------------------------------------------ #


class TestWsReconnectorHealthy:
    """Tests when WS is healthy — no reconnect should happen."""

    def test_no_reconnect_when_healthy(self):
        bot = _make_bot(all_stale=False)
        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._last_check = 0  # force check
        reconnector._check_interval = 0

        reconnector.maybe_reconnect(bot)

        # No teardown should happen
        bot.ws_feed.stop.assert_not_called()
        assert reconnector._consecutive_failures == 0

    def test_no_reconnect_when_no_ws_feed(self):
        bot = _make_bot()
        bot.ws_feed = None
        reconnector = WsReconnector()

        reconnector.maybe_reconnect(bot)
        assert reconnector._reconnect_count == 0

    def test_skip_check_within_interval(self):
        bot = _make_bot(all_stale=True)
        reconnector = WsReconnector()
        reconnector._last_check = time.monotonic()  # just checked
        reconnector._check_interval = 30.0

        reconnector.maybe_reconnect(bot)
        # Should not check — too soon
        bot.ws_feed.stale_coins.assert_not_called()


class TestWsReconnectorStale:
    """Tests when WS is stale — reconnect should happen."""

    @patch("ws.ws_reconnector.WsReconnector._rebuild")
    def test_reconnect_when_all_stale(self, mock_rebuild):
        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        # Capture references before teardown sets them to None
        imb_guard = bot.imbalance_guard
        bbo_guard = bot.bbo_guard
        fill_feed = bot.fill_feed
        ws_feed = bot.ws_feed

        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._last_check = 0
        reconnector._check_interval = 0

        reconnector.maybe_reconnect(bot)

        # Teardown should have stopped all components
        imb_guard.stop.assert_called_once()
        bbo_guard.stop.assert_called_once()
        fill_feed.stop.assert_called_once()
        ws_feed.stop.assert_called_once()

        # Rebuild should have been called
        mock_rebuild.assert_called_once_with(bot)
        assert reconnector._reconnect_count == 1

    @patch("ws.ws_reconnector.WsReconnector._rebuild")
    def test_backoff_on_failure(self, mock_rebuild):
        mock_rebuild.side_effect = Exception("connection refused")

        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._last_check = 0
        reconnector._check_interval = 0

        reconnector.maybe_reconnect(bot)
        assert reconnector._consecutive_failures == 1
        assert reconnector._reconnect_count == 0

        # Next attempt should be blocked by backoff
        reconnector._last_check = 0
        reconnector.maybe_reconnect(bot)
        # Still 1 failure because backoff prevented retry
        assert reconnector._consecutive_failures == 1

    @patch("ws.ws_reconnector.WsReconnector._rebuild")
    def test_exponential_backoff(self, mock_rebuild):
        mock_rebuild.side_effect = Exception("fail")

        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._check_interval = 0

        # First attempt — backoff should be 5s
        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        reconnector._last_check = 0
        reconnector.maybe_reconnect(bot)
        assert reconnector._consecutive_failures == 1

        # Force past backoff, fresh bot (teardown cleared previous)
        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        reconnector._next_retry_at = 0
        reconnector._last_check = 0
        reconnector.maybe_reconnect(bot)
        assert reconnector._consecutive_failures == 2

        # Third attempt
        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        reconnector._next_retry_at = 0
        reconnector._last_check = 0
        reconnector.maybe_reconnect(bot)
        assert reconnector._consecutive_failures == 3

    @patch("ws.ws_reconnector.WsReconnector._rebuild")
    def test_reset_failures_on_success(self, mock_rebuild):
        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._check_interval = 0
        reconnector._last_check = 0

        reconnector.maybe_reconnect(bot)
        assert reconnector._consecutive_failures == 0
        assert reconnector._reconnect_count == 1


class TestWsReconnectorTeardown:
    """Tests for the teardown phase."""

    @patch("ws.ws_reconnector.WsReconnector._rebuild")
    def test_teardown_stops_all_components(self, mock_rebuild):
        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        imb_guard = bot.imbalance_guard
        bbo_guard = bot.bbo_guard
        fill_feed = bot.fill_feed
        ws_feed = bot.ws_feed

        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._check_interval = 0
        reconnector._last_check = 0

        reconnector.maybe_reconnect(bot)

        imb_guard.stop.assert_called_once()
        bbo_guard.stop.assert_called_once()
        fill_feed.stop.assert_called_once()
        ws_feed.stop.assert_called_once()

    @patch("ws.ws_reconnector.WsReconnector._rebuild")
    def test_teardown_handles_missing_guards(self, mock_rebuild):
        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        bot.imbalance_guard = None
        bot.bbo_guard = None
        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._check_interval = 0
        reconnector._last_check = 0

        # Should not raise
        reconnector.maybe_reconnect(bot)
        mock_rebuild.assert_called_once()

    @patch("ws.ws_reconnector.WsReconnector._rebuild")
    def test_teardown_stops_ws_manager(self, mock_rebuild):
        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        ws_mgr = bot.ws_feed.info.ws_manager
        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._check_interval = 0
        reconnector._last_check = 0

        reconnector.maybe_reconnect(bot)
        ws_mgr.stop.assert_called_once()


class TestWsReconnectorStats:
    """Tests for observability."""

    def test_initial_stats(self):
        reconnector = WsReconnector()
        assert reconnector.stats == {
            "reconnect_count": 0,
            "consecutive_failures": 0,
        }

    @patch("ws.ws_reconnector.WsReconnector._rebuild")
    def test_stats_after_reconnect(self, mock_rebuild):
        bot = _make_bot(all_stale=True, ws_thread_alive=False)
        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._check_interval = 0
        reconnector._last_check = 0

        reconnector.maybe_reconnect(bot)
        assert reconnector.stats["reconnect_count"] == 1
        assert reconnector.stats["consecutive_failures"] == 0


class TestWsReconnectorRecovery:
    """Tests for recovery detection after reconnect."""

    def test_recovery_resets_failures(self):
        bot = _make_bot(all_stale=False)
        reconnector = WsReconnector(stale_threshold=60.0)
        reconnector._check_interval = 0
        reconnector._last_check = 0
        reconnector._consecutive_failures = 3  # simulate previous failures

        reconnector.maybe_reconnect(bot)
        assert reconnector._consecutive_failures == 0
