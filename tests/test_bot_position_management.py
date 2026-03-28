"""Tests for bot.py position management: _close_position, _close_all_positions, _check_per_trade_stops."""

from unittest.mock import MagicMock
from order_manager import OrderSide


def _make_bot():
    """Create a minimal HyperliquidBot-like object with mocked dependencies.

    We don't instantiate HyperliquidBot directly because __init__ requires
    live API connections. Instead we import the class and bind its methods
    to a mock that has the required attributes.
    """
    from bot import HyperliquidBot

    bot = MagicMock()
    bot.order_manager = MagicMock()
    bot.market_data = MagicMock()
    bot.risk_manager = MagicMock()

    # Bind the real methods to the mock object
    bot._close_position = HyperliquidBot._close_position.__get__(bot)
    bot._close_all_positions = HyperliquidBot._close_all_positions.__get__(bot)
    bot._check_per_trade_stops = HyperliquidBot._check_per_trade_stops.__get__(bot)

    return bot


class TestClosePosition:

    def test_close_long_position(self):
        bot = _make_bot()
        bot.market_data.get_sz_decimals.return_value = 3
        bot.order_manager.create_market_order.return_value = MagicMock()

        pos = {'coin': 'BTC', 'szi': '0.5'}
        result = bot._close_position(pos)

        assert result is True
        bot.order_manager.create_market_order.assert_called_once_with(
            coin='BTC', side=OrderSide.SELL, size=0.5, reduce_only=True,
        )

    def test_close_short_position(self):
        bot = _make_bot()
        bot.market_data.get_sz_decimals.return_value = 3
        bot.order_manager.create_market_order.return_value = MagicMock()

        pos = {'coin': 'ETH', 'szi': '-2.0'}
        result = bot._close_position(pos)

        assert result is True
        bot.order_manager.create_market_order.assert_called_once_with(
            coin='ETH', side=OrderSide.BUY, size=2.0, reduce_only=True,
        )

    def test_zero_size_returns_false(self):
        bot = _make_bot()
        pos = {'coin': 'SOL', 'szi': '0'}
        result = bot._close_position(pos)
        assert result is False
        bot.order_manager.create_market_order.assert_not_called()

    def test_order_failure_returns_false(self):
        bot = _make_bot()
        bot.market_data.get_sz_decimals.return_value = 3
        bot.order_manager.create_market_order.return_value = None

        pos = {'coin': 'BTC', 'szi': '1.0'}
        result = bot._close_position(pos)
        assert result is False

    def test_size_rounded_to_sz_decimals(self):
        bot = _make_bot()
        bot.market_data.get_sz_decimals.return_value = 2
        bot.order_manager.create_market_order.return_value = MagicMock()

        pos = {'coin': 'BTC', 'szi': '0.12345'}
        bot._close_position(pos)

        call_args = bot.order_manager.create_market_order.call_args
        assert call_args.kwargs['size'] == 0.12  # rounded to 2 decimals


class TestCloseAllPositions:

    def test_closes_all(self):
        bot = _make_bot()
        bot.market_data.get_sz_decimals.return_value = 3
        bot.order_manager.create_market_order.return_value = MagicMock()
        bot.order_manager.get_all_positions.return_value = [
            {'coin': 'BTC', 'szi': '0.5'},
            {'coin': 'ETH', 'szi': '-2.0'},
        ]

        bot._close_all_positions()

        assert bot.order_manager.create_market_order.call_count == 2

    def test_no_positions(self):
        bot = _make_bot()
        bot.order_manager.get_all_positions.return_value = []
        bot._close_all_positions()
        bot.order_manager.create_market_order.assert_not_called()

    def test_exception_does_not_raise(self):
        bot = _make_bot()
        bot.order_manager.get_all_positions.side_effect = Exception("API error")
        # Should not raise
        bot._close_all_positions()


class TestCheckPerTradeStops:

    def test_disabled_when_none(self):
        bot = _make_bot()
        bot.risk_manager.per_trade_stop_loss = None
        bot._check_per_trade_stops()
        bot.order_manager.get_all_positions.assert_not_called()

    def test_closes_losing_positions(self):
        bot = _make_bot()
        bot.risk_manager.per_trade_stop_loss = 0.05
        bot.market_data.get_sz_decimals.return_value = 3
        bot.order_manager.create_market_order.return_value = MagicMock()

        losing_pos = {
            'coin': 'BTC', 'szi': '1.0', 'entryPx': '50000',
            'unrealizedPnl': '-5000', 'positionValue': '50000',
        }
        bot.order_manager.get_all_positions.return_value = [losing_pos]
        bot.risk_manager.check_per_trade_stop_loss.return_value = [losing_pos]

        bot._check_per_trade_stops()

        bot.order_manager.create_market_order.assert_called_once()

    def test_no_positions_to_close(self):
        bot = _make_bot()
        bot.risk_manager.per_trade_stop_loss = 0.05
        bot.order_manager.get_all_positions.return_value = []
        bot.risk_manager.check_per_trade_stop_loss.return_value = []
        bot._check_per_trade_stops()
        bot.order_manager.create_market_order.assert_not_called()

    def test_exception_does_not_raise(self):
        bot = _make_bot()
        bot.risk_manager.per_trade_stop_loss = 0.05
        bot.order_manager.get_all_positions.side_effect = Exception("API error")
        # Should not raise
        bot._check_per_trade_stops()
