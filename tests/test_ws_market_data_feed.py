"""Tests for WebSocket MarketDataFeed and MarketDataManager.update_from_ws."""

import time
from unittest.mock import MagicMock, patch

from market_data import MarketDataManager, MarketData
from ttl_cache import TTLCacheMap
from ws.market_data_feed import MarketDataFeed


def _make_l2_levels(bid_px=99.99, ask_px=100.01, bid_sz=10, ask_sz=10):
    """Create a minimal L2 levels structure."""
    return [
        [{"px": str(bid_px), "sz": str(bid_sz)}],
        [{"px": str(ask_px), "sz": str(ask_sz)}],
    ]


class TestUpdateFromWs:
    """MarketDataManager.update_from_ws populates the cache."""

    def _make_mdm(self):
        mdm = MarketDataManager.__new__(MarketDataManager)
        mdm._cache = TTLCacheMap(ttl=2.0)
        mdm._cache_ttl = 2.0
        mdm._imbalance_depth = 5
        return mdm

    def test_basic_update(self):
        mdm = self._make_mdm()
        levels = _make_l2_levels()
        mdm.update_from_ws("BTC", levels)

        md = mdm._cache.get("BTC")
        assert md is not None
        assert md.bid == 99.99
        assert md.ask == 100.01
        assert abs(md.mid_price - 100.0) < 0.01

    def test_cache_is_fresh_after_ws_update(self):
        mdm = self._make_mdm()
        levels = _make_l2_levels()
        mdm.update_from_ws("ETH", levels)

        # Cache should be considered fresh (get returns non-None)
        assert mdm._cache.get("ETH") is not None

    def test_get_market_data_returns_ws_cached(self):
        mdm = self._make_mdm()
        levels = _make_l2_levels(bid_px=50.0, ask_px=50.1)
        mdm.update_from_ws("SOL", levels)

        # get_market_data should return the WS-cached data without REST call
        with patch.object(mdm, "get_l2_snapshot") as mock_rest:
            result = mdm.get_market_data("SOL")
            mock_rest.assert_not_called()

        assert result is not None
        assert result.bid == 50.0
        assert result.ask == 50.1

    def test_book_imbalance_computed(self):
        mdm = self._make_mdm()
        # Bid-heavy: 100 vs 10
        levels = _make_l2_levels(bid_sz=100, ask_sz=10)
        mdm.update_from_ws("BTC", levels)

        md = mdm._cache.get("BTC")
        assert md.book_imbalance > 0.8  # bid-heavy

    def test_invalid_levels_ignored(self):
        mdm = self._make_mdm()
        mdm.update_from_ws("BTC", [])  # empty
        assert mdm._cache.get("BTC") is None

        mdm.update_from_ws("BTC", [[], []])  # empty bids/asks
        assert mdm._cache.get("BTC") is None

    def test_overwrites_stale_cache(self):
        mdm = self._make_mdm()
        # First update
        mdm.update_from_ws("BTC", _make_l2_levels(bid_px=99.0, ask_px=101.0))
        assert mdm._cache.get("BTC").bid == 99.0

        # Second update overwrites
        mdm.update_from_ws("BTC", _make_l2_levels(bid_px=99.5, ask_px=100.5))
        assert mdm._cache.get("BTC").bid == 99.5


class TestParseLevels:
    """_parse_levels shared between REST and WS paths."""

    def _make_mdm(self):
        mdm = MarketDataManager.__new__(MarketDataManager)
        mdm._imbalance_depth = 5
        return mdm

    def test_returns_none_for_empty(self):
        mdm = self._make_mdm()
        assert mdm._parse_levels("BTC", []) is None
        assert mdm._parse_levels("BTC", [[], []]) is None

    def test_returns_market_data(self):
        mdm = self._make_mdm()
        levels = _make_l2_levels(bid_px=100, ask_px=102)
        md = mdm._parse_levels("BTC", levels)
        assert md is not None
        assert md.symbol == "BTC"
        assert md.spread == 2.0


class TestMarketDataFeed:
    """MarketDataFeed lifecycle and callback handling."""

    def _make_feed(self, coins=None):
        info = MagicMock()
        info.ws_manager = MagicMock()  # Not None = WS available
        info.subscribe.side_effect = lambda sub, cb: hash(sub["coin"]) % 1000

        mdm = MagicMock()
        coins = coins or ["BTC", "ETH"]
        feed = MarketDataFeed(info, mdm, coins)
        return feed, info, mdm

    def test_start_subscribes_all_coins(self):
        feed, info, _ = self._make_feed(["BTC", "ETH", "SOL"])
        feed.start()

        assert info.subscribe.call_count == 3
        assert feed.is_running
        assert feed.stats["subscriptions"] == 3

    def test_stop_unsubscribes(self):
        feed, info, _ = self._make_feed()
        feed.start()
        feed.stop()

        assert info.unsubscribe.call_count == 2
        assert not feed.is_running

    def test_callback_updates_market_data(self):
        feed, info, mdm = self._make_feed(["BTC"])
        feed.start()

        # Simulate WS callback
        callback = info.subscribe.call_args[0][1]
        callback({
            "data": {
                "coin": "BTC",
                "levels": _make_l2_levels(),
            }
        })

        mdm.update_from_ws.assert_called_once()
        assert feed.stats["updates"] == 1

    def test_callback_ignores_empty_data(self):
        feed, info, mdm = self._make_feed(["BTC"])
        feed.start()

        callback = info.subscribe.call_args[0][1]
        callback({"data": {}})  # no coin

        mdm.update_from_ws.assert_not_called()
        assert feed.stats["updates"] == 0

    def test_callback_handles_errors(self):
        feed, info, mdm = self._make_feed(["BTC"])
        feed.start()

        mdm.update_from_ws.side_effect = ValueError("parse error")

        callback = info.subscribe.call_args[0][1]
        callback({
            "data": {
                "coin": "BTC",
                "levels": _make_l2_levels(),
            }
        })

        assert feed.stats["errors"] == 1

    def test_no_ws_manager_disables_feed(self):
        info = MagicMock()
        info.ws_manager = None  # WS not available
        mdm = MagicMock()
        feed = MarketDataFeed(info, mdm, ["BTC"])
        feed.start()

        assert not feed.is_running
        info.subscribe.assert_not_called()

    def test_stale_coins(self):
        feed, info, mdm = self._make_feed(["BTC", "ETH"])
        feed.start()

        now = time.monotonic()
        # BTC updated just now, ETH updated long ago
        feed._last_update["BTC"] = now
        feed._last_update["ETH"] = now - 60  # 60s ago

        stale = feed.stale_coins(max_age=1.0)
        assert "ETH" in stale
        assert "BTC" not in stale
