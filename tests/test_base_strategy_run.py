"""Tests for BaseStrategy: run loop, execute_signal, should_close_position."""

from unittest.mock import MagicMock
from strategies.base_strategy import BaseStrategy
from order_manager import OrderSide


class ConcreteStrategy(BaseStrategy):
    """Minimal concrete strategy for testing base class methods."""

    def generate_signals(self, coin):
        return self._test_signal

    def calculate_position_size(self, coin, signal):
        return self._test_position_size


def _make_strategy(config=None, test_signal=None, test_position_size=100.0):
    config = config or {
        'take_profit_percent': 5,
        'stop_loss_percent': 2,
    }
    market_data = MagicMock()
    market_data.round_size.side_effect = (
        lambda coin, size: round(size, market_data.get_sz_decimals(coin))
    )
    market_data.get_sz_decimals.return_value = 0
    market_data.price_rounding_params.return_value = (0, True)
    order_manager = MagicMock()
    strategy = ConcreteStrategy(market_data, order_manager, config)
    strategy._test_signal = test_signal
    strategy._test_position_size = test_position_size
    return strategy


# ------------------------------------------------------------------ #
#  should_close_position
# ------------------------------------------------------------------ #

class TestShouldClosePosition:

    def test_take_profit_triggered(self):
        strategy = _make_strategy(config={
            'take_profit_percent': 5, 'stop_loss_percent': 2,
        })
        strategy.positions = {
            'BTC': {'size': 1.0, 'entry_price': 50000,
                    'unrealized_pnl': 600, 'margin_used': 5000}
        }
        strategy.market_data.get_market_data.return_value = MagicMock()
        assert strategy.should_close_position('BTC') is True

    def test_stop_loss_triggered(self):
        strategy = _make_strategy(config={
            'take_profit_percent': 5, 'stop_loss_percent': 2,
        })
        strategy.positions = {
            'ETH': {'size': 10.0, 'entry_price': 3000,
                    'unrealized_pnl': -200, 'margin_used': 3000}
        }
        strategy.market_data.get_market_data.return_value = MagicMock()
        assert strategy.should_close_position('ETH') is True

    def test_position_in_range(self):
        strategy = _make_strategy(config={
            'take_profit_percent': 5, 'stop_loss_percent': 2,
        })
        strategy.positions = {
            'SOL': {'size': 100, 'entry_price': 100,
                    'unrealized_pnl': 50, 'margin_used': 5000}
        }
        strategy.market_data.get_market_data.return_value = MagicMock()
        assert strategy.should_close_position('SOL') is False

    def test_no_position(self):
        strategy = _make_strategy()
        assert strategy.should_close_position('BTC') is False

    def test_no_market_data(self):
        strategy = _make_strategy()
        strategy.positions = {
            'BTC': {'size': 1.0, 'entry_price': 50000,
                    'unrealized_pnl': 600, 'margin_used': 5000}
        }
        strategy.market_data.get_market_data.return_value = None
        assert strategy.should_close_position('BTC') is False


# ------------------------------------------------------------------ #
#  execute_signal
# ------------------------------------------------------------------ #

class TestExecuteSignal:

    def test_market_order(self):
        strategy = _make_strategy(test_position_size=0.1)
        strategy.market_data.get_sz_decimals.return_value = 3
        strategy.market_data.get_market_data.return_value = MagicMock(bid=50000, ask=50100)

        signal = {'side': 'buy', 'order_type': 'market'}
        strategy.execute_signal('BTC', signal)

        strategy.order_manager.create_market_order.assert_called_once()
        call_kwargs = strategy.order_manager.create_market_order.call_args.kwargs
        assert call_kwargs['coin'] == 'BTC'
        assert call_kwargs['side'] == OrderSide.BUY

    def test_limit_order(self):
        strategy = _make_strategy(test_position_size=0.1)
        strategy.market_data.get_sz_decimals.return_value = 3
        strategy.market_data.get_market_data.return_value = MagicMock(bid=50000, ask=50100)

        signal = {'side': 'sell', 'order_type': 'limit'}
        strategy.execute_signal('BTC', signal)

        strategy.order_manager.create_limit_order.assert_called_once()
        call_kwargs = strategy.order_manager.create_limit_order.call_args.kwargs
        assert call_kwargs['price'] == 50100  # ask price for sell

    def test_empty_signal_does_nothing(self):
        strategy = _make_strategy()
        strategy.execute_signal('BTC', {})
        strategy.order_manager.create_market_order.assert_not_called()
        strategy.order_manager.create_limit_order.assert_not_called()

    def test_none_signal_does_nothing(self):
        strategy = _make_strategy()
        strategy.execute_signal('BTC', None)
        strategy.order_manager.create_market_order.assert_not_called()

    def test_zero_position_size_does_nothing(self):
        strategy = _make_strategy(test_position_size=0)
        strategy.market_data.get_sz_decimals.return_value = 3
        signal = {'side': 'buy', 'order_type': 'market'}
        strategy.execute_signal('BTC', signal)
        strategy.order_manager.create_market_order.assert_not_called()

    def test_no_market_data_does_nothing(self):
        strategy = _make_strategy(test_position_size=0.1)
        strategy.market_data.get_sz_decimals.return_value = 3
        strategy.market_data.get_market_data.return_value = None

        signal = {'side': 'buy', 'order_type': 'limit'}
        strategy.execute_signal('BTC', signal)
        strategy.order_manager.create_limit_order.assert_not_called()


# ------------------------------------------------------------------ #
#  run
# ------------------------------------------------------------------ #

class TestRun:

    def test_closes_positions_then_generates_signals(self):
        signal = {'side': 'buy', 'order_type': 'market'}
        strategy = _make_strategy(test_signal=signal, test_position_size=0.1)
        strategy.market_data.get_sz_decimals.return_value = 3
        strategy.market_data.get_market_data.return_value = MagicMock(bid=100, ask=101)
        strategy.order_manager.get_all_positions.return_value = []

        strategy.run(['BTC', 'ETH'])

        # update_positions was called (via get_all_positions)
        strategy.order_manager.get_all_positions.assert_called_once()
        # Signals generated for both coins
        assert strategy.order_manager.create_market_order.call_count == 2

    def test_closing_position_skips_signal(self):
        strategy = _make_strategy(
            config={'take_profit_percent': 5, 'stop_loss_percent': 2},
            test_signal={'side': 'buy', 'order_type': 'market'},
            test_position_size=0.1,
        )
        # BTC has a profitable position -> should close
        strategy.order_manager.get_all_positions.return_value = [
            {'coin': 'BTC', 'szi': '1.0', 'entryPx': '50000',
             'unrealizedPnl': '5000', 'marginUsed': '5000'},
        ]
        strategy.market_data.get_market_data.return_value = MagicMock(bid=55000, ask=55100)
        strategy.market_data.get_sz_decimals.return_value = 3

        strategy.run(['BTC'])

        # Should close, not open new
        strategy.order_manager.create_market_order.assert_called_once()
        call_kwargs = strategy.order_manager.create_market_order.call_args.kwargs
        assert call_kwargs['reduce_only'] is True

    def test_no_signal_no_order(self):
        strategy = _make_strategy(test_signal=None)
        strategy.order_manager.get_all_positions.return_value = []

        strategy.run(['BTC'])

        strategy.order_manager.create_market_order.assert_not_called()
        strategy.order_manager.create_limit_order.assert_not_called()
