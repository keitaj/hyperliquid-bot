"""Unit tests for strategy calculations (no network required)."""

import pandas as pd
import numpy as np


class TestRSICalculation:
    """Test RSI calculation edge cases."""

    def _make_strategy(self, config=None):
        """Create an RSIStrategy with mocked dependencies."""
        from strategies.rsi_strategy import RSIStrategy
        strategy = RSIStrategy.__new__(RSIStrategy)
        strategy.rsi_period = (config or {}).get('rsi_period', 14)
        return strategy

    def test_rsi_all_gains_no_division_by_zero(self):
        """RSI should be 100 (or NaN) when all deltas are positive, not crash."""
        strategy = self._make_strategy()
        # 20 monotonically increasing closes
        df = pd.DataFrame({'close': np.arange(100.0, 120.0)})
        result = strategy.calculate_rsi(df)
        # Should not raise; RSI should be 100 or NaN (not inf)
        rsi_values = result['rsi'].dropna()
        assert all((v == 100.0 or np.isnan(v)) for v in rsi_values)

    def test_rsi_all_losses(self):
        """RSI should be 0 when all deltas are negative."""
        strategy = self._make_strategy()
        df = pd.DataFrame({'close': np.arange(120.0, 100.0, -1.0)})
        result = strategy.calculate_rsi(df)
        rsi_values = result['rsi'].dropna()
        assert all(v == 0.0 for v in rsi_values)

    def test_rsi_normal_range(self):
        """RSI should be between 0 and 100."""
        strategy = self._make_strategy()
        np.random.seed(42)
        closes = 100 + np.cumsum(np.random.randn(50))
        df = pd.DataFrame({'close': closes})
        result = strategy.calculate_rsi(df)
        rsi_values = result['rsi'].dropna()
        assert all(0 <= v <= 100 for v in rsi_values)


class TestBollingerBandsCalculation:
    """Test Bollinger Bands calculation."""

    def _make_strategy(self):
        from strategies.bollinger_bands_strategy import BollingerBandsStrategy
        strategy = BollingerBandsStrategy.__new__(BollingerBandsStrategy)
        strategy.bb_period = 20
        strategy.std_dev = 2
        return strategy

    def test_upper_band_above_lower(self):
        """Upper band should always be above lower band."""
        strategy = self._make_strategy()
        np.random.seed(42)
        closes = 100 + np.cumsum(np.random.randn(50))
        df = pd.DataFrame({'close': closes})
        result = strategy.calculate_bollinger_bands(df)
        valid = result.dropna()
        assert all(valid['upper_band'] > valid['lower_band'])

    def test_price_position_range(self):
        """Price position should be between 0 and 1 for normal values."""
        strategy = self._make_strategy()
        np.random.seed(42)
        closes = 100 + np.cumsum(np.random.randn(50) * 0.5)
        df = pd.DataFrame({'close': closes})
        result = strategy.calculate_bollinger_bands(df)
        valid = result.dropna()
        # Most values should be within [0, 1] but outliers are allowed
        within_range = ((valid['price_position'] >= 0) & (valid['price_position'] <= 1)).mean()
        assert within_range > 0.7  # At least 70% within bands


class TestMACDCalculation:
    """Test MACD calculation."""

    def _make_strategy(self):
        from strategies.macd_strategy import MACDStrategy
        strategy = MACDStrategy.__new__(MACDStrategy)
        strategy.fast_ema = 12
        strategy.slow_ema = 26
        strategy.signal_ema = 9
        strategy.divergence_lookback = 20
        return strategy

    def test_macd_histogram_equals_diff(self):
        """Histogram should equal MACD line minus signal line."""
        strategy = self._make_strategy()
        np.random.seed(42)
        closes = 100 + np.cumsum(np.random.randn(60))
        df = pd.DataFrame({
            'close': closes,
            'high': closes + 1,
            'low': closes - 1,
        })
        result = strategy.calculate_macd(df)
        valid = result.dropna()
        diff = (valid['macd_line'] - valid['signal_line']).values
        hist = valid['macd_histogram'].values
        np.testing.assert_allclose(diff, hist, atol=1e-10)


class TestMovingAverageCalculation:
    """Test Simple MA calculation."""

    def _make_strategy(self):
        from strategies.simple_ma_strategy import SimpleMAStrategy
        strategy = SimpleMAStrategy.__new__(SimpleMAStrategy)
        strategy.fast_period = 10
        strategy.slow_period = 30
        return strategy

    def test_fast_ma_responds_faster(self):
        """Fast MA should react more quickly to a price jump."""
        strategy = self._make_strategy()
        prices = [100.0] * 30 + [110.0] * 5
        df = pd.DataFrame({'close': prices})
        result = strategy.calculate_moving_averages(df)
        last = result.iloc[-1]
        assert last['ma_fast'] > last['ma_slow']
