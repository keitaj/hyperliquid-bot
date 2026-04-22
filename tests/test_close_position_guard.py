"""Tests for MarketMakingStrategy.close_position() _open_positions guard."""

import time
from unittest.mock import MagicMock

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(**extra):
    """Create a MarketMakingStrategy with mocked dependencies."""
    market_data = MagicMock()
    order_manager = MagicMock()
    config = {
        'spread_bps': 10,
        'order_size_usd': 200,
        'max_open_orders': 4,
        'close_immediately': True,
        'max_positions': 8,
        'maker_only': True,
        'bbo_mode': True,
        'bbo_offset_bps': 1.0,
        **extra,
    }
    strategy = MarketMakingStrategy(market_data, order_manager, config)
    return strategy


class TestClosePositionGuard:
    """close_position() skips reduce_only order when _open_positions is empty."""

    def test_skips_when_not_in_open_positions(self):
        """If FillFeed already removed coin from _open_positions, skip close."""
        strategy = _make_strategy()
        strategy.positions = {'BTC': {'size': 0.5, 'entry_price': 100}}
        # _open_positions is empty (FillFeed called on_position_closed)
        strategy._closer._open_positions = {}

        strategy.close_position('BTC')

        # Should NOT call order_manager
        strategy.order_manager.create_market_order.assert_not_called()
        # Should clean up positions cache
        assert 'BTC' not in strategy.positions

    def test_proceeds_when_in_open_positions(self):
        """If coin is in _open_positions, proceed with close."""
        strategy = _make_strategy()
        strategy.positions = {'BTC': {'size': 0.5, 'entry_price': 100}}
        strategy._closer._open_positions = {'BTC': (time.monotonic(), None, 0)}

        # Mock get_all_positions for the fresh check in super().close_position()
        strategy.order_manager.get_all_positions.return_value = [
            {'coin': 'BTC', 'szi': '0.5'}
        ]
        strategy.market_data.round_size.return_value = 0.5

        strategy.close_position('BTC')

        # Should call create_market_order (via close_position_market)
        strategy.order_manager.create_market_order.assert_called_once()

    def test_skips_when_coin_not_tracked_at_all(self):
        """If coin was never tracked in _open_positions, skip close."""
        strategy = _make_strategy()
        strategy.positions = {'ETH': {'size': 1.0, 'entry_price': 3000}}
        strategy._closer._open_positions = {'BTC': (time.monotonic(), None, 0)}

        strategy.close_position('ETH')

        strategy.order_manager.create_market_order.assert_not_called()
        assert 'ETH' not in strategy.positions
