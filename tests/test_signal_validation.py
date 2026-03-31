"""Tests for BaseStrategy._validate_signal()."""

from unittest.mock import MagicMock
from strategies.base_strategy import BaseStrategy


class ConcreteStrategy(BaseStrategy):
    """Minimal concrete strategy for testing base class methods."""

    def generate_signals(self, coin):
        return self._test_signal

    def calculate_position_size(self, coin, signal):
        return self._test_position_size


def _make_strategy():
    config = {'take_profit_percent': 5, 'stop_loss_percent': 2}
    market_data = MagicMock()
    order_manager = MagicMock()
    strategy = ConcreteStrategy(market_data, order_manager, config)
    strategy._test_signal = None
    strategy._test_position_size = 100.0
    return strategy


# ------------------------------------------------------------------ #
#  Valid signals
# ------------------------------------------------------------------ #

class TestValidSignals:

    def test_valid_signal_passes_through(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'confidence': 0.8, 'order_type': 'market'}
        result = strategy._validate_signal(signal)
        assert result is signal

    def test_valid_sell_signal(self):
        strategy = _make_strategy()
        signal = {'side': 'sell', 'confidence': 0.5, 'order_type': 'limit'}
        result = strategy._validate_signal(signal)
        assert result is signal

    def test_default_confidence_passes(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'order_type': 'limit'}
        result = strategy._validate_signal(signal)
        assert result is signal

    def test_default_order_type_passes(self):
        strategy = _make_strategy()
        signal = {'side': 'sell', 'confidence': 0.7}
        result = strategy._validate_signal(signal)
        assert result is signal

    def test_confidence_zero_passes(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'confidence': 0.0}
        result = strategy._validate_signal(signal)
        assert result is signal

    def test_confidence_one_passes(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'confidence': 1.0}
        result = strategy._validate_signal(signal)
        assert result is signal

    def test_confidence_half_passes(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'confidence': 0.5}
        result = strategy._validate_signal(signal)
        assert result is signal


# ------------------------------------------------------------------ #
#  Invalid side
# ------------------------------------------------------------------ #

class TestInvalidSide:

    def test_uppercase_buy_rejected(self):
        strategy = _make_strategy()
        signal = {'side': 'BUY', 'confidence': 0.5, 'order_type': 'market'}
        assert strategy._validate_signal(signal) is None

    def test_long_rejected(self):
        strategy = _make_strategy()
        signal = {'side': 'long', 'confidence': 0.5, 'order_type': 'market'}
        assert strategy._validate_signal(signal) is None

    def test_missing_side_rejected(self):
        strategy = _make_strategy()
        signal = {'confidence': 0.5, 'order_type': 'market'}
        assert strategy._validate_signal(signal) is None


# ------------------------------------------------------------------ #
#  Invalid confidence
# ------------------------------------------------------------------ #

class TestInvalidConfidence:

    def test_negative_confidence_rejected(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'confidence': -0.5}
        assert strategy._validate_signal(signal) is None

    def test_confidence_above_one_rejected(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'confidence': 2.0}
        assert strategy._validate_signal(signal) is None

    def test_nan_confidence_rejected(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'confidence': float('nan')}
        assert strategy._validate_signal(signal) is None

    def test_inf_confidence_rejected(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'confidence': float('inf')}
        assert strategy._validate_signal(signal) is None


# ------------------------------------------------------------------ #
#  Invalid order_type
# ------------------------------------------------------------------ #

class TestInvalidOrderType:

    def test_unknown_order_type_rejected(self):
        strategy = _make_strategy()
        signal = {'side': 'buy', 'order_type': 'unknown'}
        assert strategy._validate_signal(signal) is None
