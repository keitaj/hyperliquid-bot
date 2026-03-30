"""Tests for OrderManager user_state TTL cache."""

from unittest.mock import MagicMock, patch
import pytest

from order_manager import OrderManager


def _make_user_state(positions=None):
    """Build a mock user_state response."""
    asset_positions = []
    for p in (positions or []):
        asset_positions.append({'position': p})
    return {'assetPositions': asset_positions, 'marginSummary': {}}


@pytest.fixture(autouse=True)
def _bypass_api_wrapper():
    with patch('order_manager.api_wrapper') as mock_wrapper:
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        yield mock_wrapper


def _make_order_manager(user_state_cache_ttl=5.0):
    exchange = MagicMock()
    info = MagicMock()
    return OrderManager(
        exchange, info, '0xabc',
        user_state_cache_ttl=user_state_cache_ttl,
    )


class TestUserStateCache:

    def test_cache_avoids_duplicate_calls(self):
        """get_position + get_all_positions in same cycle should share one API call."""
        mgr = _make_order_manager()
        mgr.info.user_state.return_value = _make_user_state([
            {'coin': 'BTC', 'szi': '0.1', 'entryPx': '50000', 'unrealizedPnl': '10', 'marginUsed': '100'},
        ])

        pos = mgr.get_position('BTC')
        all_pos = mgr.get_all_positions()

        assert pos is not None
        assert pos['coin'] == 'BTC'
        assert len(all_pos) == 1
        assert mgr.info.user_state.call_count == 1

    def test_get_all_positions_twice_uses_cache(self):
        """Two consecutive get_all_positions calls should hit API once."""
        mgr = _make_order_manager()
        mgr.info.user_state.return_value = _make_user_state([
            {'coin': 'ETH', 'szi': '1.0', 'entryPx': '3000', 'unrealizedPnl': '0', 'marginUsed': '50'},
        ])

        mgr.get_all_positions()
        mgr.get_all_positions()

        assert mgr.info.user_state.call_count == 1

    def test_cache_expires(self):
        """After TTL expires, the next call should hit the API again."""
        mgr = _make_order_manager(user_state_cache_ttl=0.0)
        mgr.info.user_state.return_value = _make_user_state()

        mgr.get_all_positions()
        mgr.get_all_positions()

        assert mgr.info.user_state.call_count == 2

    def test_get_position_not_found(self):
        """get_position returns None for a coin not in positions."""
        mgr = _make_order_manager()
        mgr.info.user_state.return_value = _make_user_state([
            {'coin': 'BTC', 'szi': '0.1', 'entryPx': '50000', 'unrealizedPnl': '0', 'marginUsed': '100'},
        ])

        assert mgr.get_position('ETH') is None
        assert mgr.get_position('BTC') is not None
        # Both calls share one API fetch
        assert mgr.info.user_state.call_count == 1

    def test_default_ttl(self):
        """Default user_state cache TTL should be 2.0 seconds."""
        mgr = _make_order_manager.__wrapped__(5.0) if hasattr(_make_order_manager, '__wrapped__') else None
        exchange = MagicMock()
        info = MagicMock()
        mgr = OrderManager(exchange, info, '0xabc')
        assert mgr._user_state_cache_ttl == 2.0
