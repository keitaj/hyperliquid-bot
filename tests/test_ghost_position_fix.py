"""Tests for ghost position bug fixes.

Fix 1: Cache invalidation on API error in MultiDexMarketData.get_user_state()
Fix 2: Auto-clear stale position tracking after consecutive close failures
"""

import time
from unittest.mock import MagicMock

from strategies.mm_position_closer import PositionCloser, _TIER_NORMAL


# ------------------------------------------------------------------ #
# Fix 1: Cache invalidation on API error
# ------------------------------------------------------------------ #


class TestUserStateCacheInvalidation:
    """get_user_state() must invalidate cache on API error so stale
    data is not returned on the next call."""

    def _make_multi_dex_md(self, cache_ttl=10.0):
        from hip3.multi_dex_market_data import MultiDexMarketData

        info = MagicMock()
        registry = MagicMock()
        md = MultiDexMarketData(
            info=info,
            registry=registry,
            api_url="https://api.hyperliquid.xyz",
            user_state_cache_ttl=cache_ttl,
        )
        return md, info

    def test_cache_invalidated_on_api_error(self):
        """After TTL expires and the API call fails, the stale cache entry
        should be removed so subsequent calls do not return old data."""
        md, info = self._make_multi_dex_md(cache_ttl=0.0)  # TTL=0 so every call hits API

        # First call succeeds
        good_state = {"assetPositions": [{"position": {"coin": "BTC", "szi": "0.1"}}]}
        info.user_state.return_value = good_state
        result1 = md.get_user_state("0xabc", dex="xyz")
        assert result1 == good_state

        # Second call raises API error -- should not return stale data
        info.user_state.side_effect = ConnectionError("timeout")
        result2 = md.get_user_state("0xabc", dex="xyz")
        assert result2 == {}

        # Third call succeeds with new data
        info.user_state.side_effect = None
        new_state = {"assetPositions": []}
        info.user_state.return_value = new_state
        result3 = md.get_user_state("0xabc", dex="xyz")
        assert result3 == new_state
        assert info.user_state.call_count == 3

    def test_cache_not_invalidated_on_success(self):
        """Successful calls should keep the cache intact."""
        md, info = self._make_multi_dex_md(cache_ttl=60.0)

        good_state = {"assetPositions": []}
        info.user_state.return_value = good_state

        md.get_user_state("0xabc", dex="xyz")
        md.get_user_state("0xabc", dex="xyz")
        # Second call should use cache
        assert info.user_state.call_count == 1

    def test_different_dex_cache_independent(self):
        """API error for one DEX should not affect another DEX's cache."""
        md, info = self._make_multi_dex_md(cache_ttl=60.0)

        state_xyz = {"dex": "xyz"}
        state_km = {"dex": "km"}
        info.user_state.return_value = state_xyz
        md.get_user_state("0xabc", dex="xyz")

        info.user_state.return_value = state_km
        md.get_user_state("0xabc", dex="km")

        # Manually expire xyz cache to force API call
        cache_key_xyz = ("0xabc", "xyz")
        md._dex_user_state_cache.invalidate(cache_key_xyz)

        # Error on xyz (now cache-expired, so it hits API)
        info.user_state.side_effect = ConnectionError("timeout")
        md.get_user_state("0xabc", dex="xyz")

        # xyz cache should be gone
        assert md._dex_user_state_cache.get(cache_key_xyz) is None
        # km cache should still be valid
        assert md._dex_user_state_cache.get(("0xabc", "km")) is not None

        info.user_state.side_effect = None
        info.user_state.return_value = state_km
        result = md.get_user_state("0xabc", dex="km")
        assert result == state_km
        # xyz(1) + km(1) + xyz error(1) = 3, km cached on 4th call = still 3
        assert info.user_state.call_count == 3


# ------------------------------------------------------------------ #
# Fix 2: Auto-clear after consecutive close failures
# ------------------------------------------------------------------ #


def _make_closer(max_age=120, maker_only=False, spread_bps=10):
    om = MagicMock()
    md = MagicMock()
    md.round_size.return_value = 0.5
    closer = PositionCloser(
        order_manager=om,
        market_data=md,
        spread_bps=spread_bps,
        max_position_age_seconds=max_age,
        maker_only=maker_only,
        taker_fallback_age_seconds=None,
    )
    return closer, om, md


class TestConsecutiveCloseFailureAutoClear:
    """After 5 consecutive manage() cycles where no close order is
    successfully placed, the stale position tracking should be cleared."""

    def test_auto_clear_after_5_failures(self):
        """Position tracking is cleared after 5 consecutive failures."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        # Simulate order placement always failing (returns None)
        om.create_limit_order.return_value = None

        position = {"size": 0.5, "entry_price": 50000.0}
        close_fn = MagicMock()

        for i in range(5):
            # Reset entry time each cycle to keep within max_age
            if "BTC" in closer._open_positions:
                entry_time = closer._open_positions["BTC"][0]
                closer._open_positions["BTC"] = (entry_time, None, _TIER_NORMAL)

            closer.manage("BTC", position, close_fn)

            if i < 4:
                assert "BTC" in closer._open_positions, f"Should still be tracked at cycle {i}"
            else:
                assert "BTC" not in closer._open_positions, "Should be cleared after 5 failures"

        assert closer._consecutive_close_failures.get("BTC") is None

    def test_counter_resets_on_successful_close_order(self):
        """A successful close order placement resets the failure counter."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        position = {"size": 0.5, "entry_price": 50000.0}
        close_fn = MagicMock()

        # 3 failures
        om.create_limit_order.return_value = None
        for _ in range(3):
            closer.manage("BTC", position, close_fn)
        assert closer._consecutive_close_failures.get("BTC") == 3

        # Successful placement
        mock_order = MagicMock()
        mock_order.id = 42
        om.create_limit_order.return_value = mock_order
        # Need to reset the close_oid to None so it tries to place again
        entry_time = closer._open_positions["BTC"][0]
        closer._open_positions["BTC"] = (entry_time, None, _TIER_NORMAL)

        closer.manage("BTC", position, close_fn)

        assert closer._consecutive_close_failures.get("BTC") is None

    def test_counter_per_coin_independent(self):
        """Failure counters are independent per coin."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        om.create_limit_order.return_value = None

        position = {"size": 0.5, "entry_price": 50000.0}
        close_fn = MagicMock()

        # 3 failures on BTC
        for _ in range(3):
            closer.manage("BTC", position, close_fn)

        # 1 failure on ETH
        closer.manage("ETH", position, close_fn)

        assert closer._consecutive_close_failures.get("BTC") == 3
        assert closer._consecutive_close_failures.get("ETH") == 1

    def test_on_position_closed_clears_failure_counter(self):
        """on_position_closed() should also clear the failure counter."""
        closer, om, md = _make_closer()

        closer._consecutive_close_failures["BTC"] = 3
        closer._open_positions["BTC"] = (time.monotonic(), None, _TIER_NORMAL)

        closer.on_position_closed("BTC")

        assert "BTC" not in closer._consecutive_close_failures
        assert "BTC" not in closer._open_positions

    def test_cleanup_closed_clears_failure_counter(self):
        """cleanup_closed() should also clear the failure counter."""
        closer, om, md = _make_closer()

        closer._consecutive_close_failures["BTC"] = 3
        closer._open_positions["BTC"] = (time.monotonic(), None, _TIER_NORMAL)

        closer.cleanup_closed("BTC")

        assert "BTC" not in closer._consecutive_close_failures
        assert "BTC" not in closer._open_positions

    def test_no_auto_clear_below_threshold(self):
        """4 failures should NOT trigger auto-clear."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        om.create_limit_order.return_value = None

        position = {"size": 0.5, "entry_price": 50000.0}
        close_fn = MagicMock()

        for _ in range(4):
            closer.manage("BTC", position, close_fn)

        assert "BTC" in closer._open_positions
        assert closer._consecutive_close_failures.get("BTC") == 4
