"""Unit tests for BaseStrategy shared helpers (_has_position, _get_candles_or_none)."""

from unittest.mock import MagicMock
import pandas as pd
import numpy as np

from strategies.simple_ma_strategy import SimpleMAStrategy


def _make_strategy(positions=None):
    """Create a SimpleMAStrategy with mocked dependencies."""
    strategy = SimpleMAStrategy.__new__(SimpleMAStrategy)
    strategy.positions = positions or {}
    strategy.market_data = MagicMock()
    strategy.candle_interval = '15m'
    strategy.lookback = 40
    return strategy


# ---------------------------------------------------------------------------
# _has_position
# ---------------------------------------------------------------------------

class TestHasPosition:

    def test_no_positions(self):
        strategy = _make_strategy()
        assert strategy._has_position('BTC') is False

    def test_coin_not_present(self):
        strategy = _make_strategy({'ETH': {'size': 1.0}})
        assert strategy._has_position('BTC') is False

    def test_coin_present_nonzero(self):
        strategy = _make_strategy({'BTC': {'size': 0.5}})
        assert strategy._has_position('BTC') is True

    def test_coin_present_negative(self):
        """Short positions (negative size) should also return True."""
        strategy = _make_strategy({'BTC': {'size': -0.3}})
        assert strategy._has_position('BTC') is True

    def test_coin_present_zero(self):
        """Zero-size position means effectively no position."""
        strategy = _make_strategy({'BTC': {'size': 0}})
        assert strategy._has_position('BTC') is False


# ---------------------------------------------------------------------------
# _get_candles_or_none
# ---------------------------------------------------------------------------

def _make_candles(n_rows: int) -> pd.DataFrame:
    return pd.DataFrame({
        'open': np.arange(n_rows, dtype=float),
        'high': np.arange(n_rows, dtype=float) + 1,
        'low': np.arange(n_rows, dtype=float) - 1,
        'close': np.arange(n_rows, dtype=float),
        'volume': np.ones(n_rows),
    })


class TestGetCandlesOrNone:

    def test_enough_candles(self):
        strategy = _make_strategy()
        strategy.market_data.get_candles.return_value = _make_candles(30)

        result = strategy._get_candles_or_none('BTC', 20)
        assert result is not None
        assert len(result) == 30

    def test_not_enough_candles(self):
        strategy = _make_strategy()
        strategy.market_data.get_candles.return_value = _make_candles(10)

        result = strategy._get_candles_or_none('BTC', 20)
        assert result is None

    def test_exact_min_periods(self):
        strategy = _make_strategy()
        strategy.market_data.get_candles.return_value = _make_candles(20)

        result = strategy._get_candles_or_none('BTC', 20)
        assert result is not None

    def test_uses_default_interval_and_lookback(self):
        strategy = _make_strategy()
        strategy.candle_interval = '5m'
        strategy.lookback = 50
        strategy.market_data.get_candles.return_value = _make_candles(50)

        strategy._get_candles_or_none('ETH', 10)
        strategy.market_data.get_candles.assert_called_once_with(
            coin='ETH', interval='5m', lookback=50,
        )

    def test_override_interval_and_lookback(self):
        strategy = _make_strategy()
        strategy.market_data.get_candles.return_value = _make_candles(100)

        strategy._get_candles_or_none('ETH', 10, interval='1h', lookback=100)
        strategy.market_data.get_candles.assert_called_once_with(
            coin='ETH', interval='1h', lookback=100,
        )
