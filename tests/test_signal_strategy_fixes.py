"""Tests for RSI/Grid production hardening fixes."""

from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np

from strategies.rsi_strategy import RSIStrategy
from strategies.grid_trading_strategy import GridTradingStrategy


def _make_rsi():
    with patch.object(RSIStrategy, '__init__', lambda self, *a, **k: None):
        s = RSIStrategy.__new__(RSIStrategy)
    s.rsi_period = 14
    s.oversold_threshold = 30
    s.overbought_threshold = 70
    s.candle_interval = '15m'
    s.lookback = 34
    s.positions = {}
    s.market_data = MagicMock()
    s.order_manager = MagicMock()
    s._last_rsi = {}
    return s


def _make_grid():
    with patch.object(GridTradingStrategy, '__init__', lambda self, *a, **k: None):
        s = GridTradingStrategy.__new__(GridTradingStrategy)
    s.grid_levels = 10
    s.grid_spacing_pct = 0.5
    s.position_size_per_grid = 100
    s.max_positions = 3
    s.range_period = 100
    s.candle_interval = '15m'
    s.range_pct_threshold = 10
    s.volatility_threshold = 0.15
    s.grid_recalc_bars = 20
    s.grid_saturation_threshold = 0.7
    s.grid_boundary_margin_low = 0.98
    s.grid_boundary_margin_high = 1.02
    s.account_cap_pct = 0.05
    s.active_grids = {}
    s.positions = {}
    s.market_data = MagicMock()
    s.order_manager = MagicMock()
    return s


class TestRSINaN:
    """RSI returns None when all prices are identical (NaN RSI)."""

    def test_identical_prices_returns_none(self):
        s = _make_rsi()
        # Create candles with identical close prices → RSI = NaN
        n = s.rsi_period + 5
        df = pd.DataFrame({
            'open': [100.0] * n,
            'high': [100.0] * n,
            'low': [100.0] * n,
            'close': [100.0] * n,
            'volume': [1000.0] * n,
        }, index=pd.date_range('2026-01-01', periods=n, freq='15min'))
        s._get_candles_or_none = MagicMock(return_value=df)

        result = s.generate_signals('BTC')
        assert result is None

    def test_normal_rsi_in_neutral_returns_none(self):
        s = _make_rsi()
        # Create candles with mild oscillation → RSI around 50 (neutral)
        n = s.rsi_period + 5
        prices = [100 + 0.1 * (i % 3 - 1) for i in range(n)]
        df = pd.DataFrame({
            'open': prices,
            'high': [p + 0.05 for p in prices],
            'low': [p - 0.05 for p in prices],
            'close': prices,
            'volume': [1000.0] * n,
        }, index=pd.date_range('2026-01-01', periods=n, freq='15min'))
        s._get_candles_or_none = MagicMock(return_value=df)

        result = s.generate_signals('BTC')
        assert result is None  # RSI in neutral zone, no signal


class TestGridKeyError:
    """Grid recalculation handles stale index gracefully."""

    def test_stale_index_forces_recalculation(self):
        s = _make_grid()

        # Create candles
        n = 60
        prices = [100 + 0.5 * np.sin(i / 5) for i in range(n)]
        df = pd.DataFrame({
            'open': prices,
            'high': [p + 0.2 for p in prices],
            'low': [p - 0.2 for p in prices],
            'close': prices,
            'volume': [1000.0] * n,
        }, index=pd.date_range('2026-01-01', periods=n, freq='15min'))
        s._get_candles_or_none = MagicMock(return_value=df)

        # Set up an active grid with a stale last_update index that
        # no longer exists in the new DataFrame
        stale_timestamp = pd.Timestamp('2025-12-01 00:00:00')
        s.active_grids['BTC'] = {
            'levels': [('buy', 99.5), ('sell', 100.5)],
            'filled_orders': {},
            'last_update': stale_timestamp,
        }

        # Should not crash — should recalculate the grid
        s.generate_signals('BTC')
        # After recalculation, active_grids should have updated last_update
        assert s.active_grids['BTC']['last_update'] != stale_timestamp

    def test_valid_index_no_recalculation(self):
        s = _make_grid()

        n = 60
        prices = [100 + 0.5 * np.sin(i / 5) for i in range(n)]
        df = pd.DataFrame({
            'open': prices,
            'high': [p + 0.2 for p in prices],
            'low': [p - 0.2 for p in prices],
            'close': prices,
            'volume': [1000.0] * n,
        }, index=pd.date_range('2026-01-01', periods=n, freq='15min'))
        s._get_candles_or_none = MagicMock(return_value=df)

        # Use a valid index from the DataFrame
        valid_timestamp = df.index[-5]
        original_levels = [('buy', 99.5), ('sell', 100.5)]
        s.active_grids['BTC'] = {
            'levels': original_levels,
            'filled_orders': {},
            'last_update': valid_timestamp,
        }

        s.generate_signals('BTC')
        # Should NOT recalculate (only 5 bars since update, threshold is 20)
        assert s.active_grids['BTC']['levels'] == original_levels
