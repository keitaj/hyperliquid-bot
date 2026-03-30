"""Tests for MarketDataManager.get_market_data TTL cache."""

from unittest.mock import MagicMock, patch
import pytest

from market_data import MarketDataManager


def _make_l2(bid: float = 50000.0, ask: float = 50100.0) -> dict:
    return {
        'levels': [
            [{'px': str(bid), 'sz': '1.0'}],
            [{'px': str(ask), 'sz': '1.0'}],
        ]
    }


@pytest.fixture(autouse=True)
def _bypass_api_wrapper():
    with patch('market_data.api_wrapper') as mock_wrapper:
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        yield mock_wrapper


class TestMarketDataCache:

    def test_cache_avoids_duplicate_l2_calls(self):
        """Second call within TTL should reuse cached result."""
        info = MagicMock()
        info.l2_snapshot.return_value = _make_l2()
        mgr = MarketDataManager(info, market_data_cache_ttl=5.0)

        md1 = mgr.get_market_data('BTC')
        md2 = mgr.get_market_data('BTC')

        assert md1 is md2
        assert info.l2_snapshot.call_count == 1

    def test_different_coins_cached_independently(self):
        """Each coin should have its own cache entry."""
        info = MagicMock()
        info.l2_snapshot.return_value = _make_l2()
        mgr = MarketDataManager(info, market_data_cache_ttl=5.0)

        mgr.get_market_data('BTC')
        mgr.get_market_data('ETH')

        assert info.l2_snapshot.call_count == 2

    def test_cache_expires_after_ttl(self):
        """After TTL expires, the next call should hit the API again."""
        info = MagicMock()
        info.l2_snapshot.return_value = _make_l2()
        # Use TTL of 0 so cache always expires
        mgr = MarketDataManager(info, market_data_cache_ttl=0.0)

        mgr.get_market_data('BTC')
        mgr.get_market_data('BTC')

        assert info.l2_snapshot.call_count == 2

    def test_cache_returns_fresh_data_after_expiry(self):
        """After cache expires, updated data should be returned."""
        info = MagicMock()
        info.l2_snapshot.return_value = _make_l2(bid=50000.0, ask=50100.0)
        mgr = MarketDataManager(info, market_data_cache_ttl=0.0)

        md1 = mgr.get_market_data('BTC')
        assert md1.bid == 50000.0

        info.l2_snapshot.return_value = _make_l2(bid=51000.0, ask=51100.0)
        md2 = mgr.get_market_data('BTC')
        assert md2.bid == 51000.0

    def test_failed_l2_does_not_cache(self):
        """When l2_snapshot returns empty, nothing should be cached."""
        info = MagicMock()
        info.l2_snapshot.return_value = {}
        mgr = MarketDataManager(info, market_data_cache_ttl=5.0)

        result = mgr.get_market_data('BTC')
        assert result is None

        # Now return valid data — should fetch again (not cached None)
        info.l2_snapshot.return_value = _make_l2()
        result = mgr.get_market_data('BTC')
        assert result is not None
        assert info.l2_snapshot.call_count == 2

    def test_default_cache_ttl(self):
        """Default TTL should be 2.0 seconds."""
        info = MagicMock()
        mgr = MarketDataManager(info)
        assert mgr._cache_ttl == 2.0
