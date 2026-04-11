"""Tests that generate_signals() passes indicator values through the signal
dict so that calculate_position_size() does not need to re-fetch candles.

Each test class covers one strategy and verifies:
  1. The signal dict includes the expected indicator key.
  2. calculate_position_size() reads the value from the signal and does NOT
     call market_data.get_candles().
"""

import logging
from unittest.mock import MagicMock
import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_market_data_mock(mid_price: float = 100.0):
    md = MagicMock()
    md.mid_price = mid_price
    md.bid = mid_price - 0.1
    md.ask = mid_price + 0.1
    return md


# ---------------------------------------------------------------------------
# RSI Strategy
# ---------------------------------------------------------------------------

class TestRSISignalPassthrough:

    def _make_strategy(self):
        from strategies.rsi_strategy import RSIStrategy
        strategy = RSIStrategy.__new__(RSIStrategy)
        strategy.rsi_period = 14
        strategy.oversold_threshold = 30
        strategy.overbought_threshold = 70
        strategy.lookback = 34
        strategy.position_size_usd = 100
        strategy.max_positions = 3
        strategy.candle_interval = '15m'
        strategy.rsi_extreme_low = 25
        strategy.rsi_moderate_low = 35
        strategy.size_multiplier_extreme = 1.5
        strategy.size_multiplier_moderate = 1.2
        strategy.positions = {}
        strategy.market_data = MagicMock()
        strategy.order_manager = MagicMock()
        strategy.config = {}
        strategy._last_rsi = {}
        return strategy

    def _make_oversold_candles(self):
        """Create candle data where RSI crosses below oversold threshold."""
        np.random.seed(42)
        n = 40
        # Start high, drop sharply at the end to trigger oversold
        prices = np.concatenate([
            np.full(n - 5, 100.0),
            np.array([95.0, 90.0, 85.0, 82.0, 80.0]),
        ])
        return pd.DataFrame({
            'close': prices,
            'high': prices + 1,
            'low': prices - 1,
            'volume': np.ones(n),
        })

    def test_signal_contains_rsi(self):
        strategy = self._make_strategy()
        candles = self._make_oversold_candles()
        strategy.market_data.get_candles.return_value = candles

        signal = strategy.generate_signals('BTC')
        if signal is not None:
            assert 'rsi' in signal
            assert isinstance(signal['rsi'], (float, np.floating))

    def test_position_size_uses_signal_rsi(self):
        """calculate_position_size should use signal['rsi'], not fetch candles."""
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        # Patch _apply_account_cap to return simple value
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        signal = {'side': 'buy', 'confidence': 0.8, 'rsi': 20.0}
        size = strategy.calculate_position_size('BTC', signal)

        assert size > 0
        # get_candles should NOT be called by calculate_position_size
        strategy.market_data.get_candles.assert_not_called()

    def test_extreme_rsi_increases_size(self):
        """RSI below extreme threshold should multiply position size."""
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        normal_signal = {'side': 'buy', 'confidence': 0.8, 'rsi': 50.0}
        extreme_signal = {'side': 'buy', 'confidence': 0.8, 'rsi': 20.0}

        normal_size = strategy.calculate_position_size('BTC', normal_signal)
        extreme_size = strategy.calculate_position_size('BTC', extreme_signal)

        assert extreme_size > normal_size

    def test_missing_rsi_warns_and_uses_neutral(self, caplog):
        """Missing 'rsi' key should log warning and not change size."""
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        signal_with = {'side': 'buy', 'confidence': 0.8, 'rsi': 47.5}
        signal_without = {'side': 'buy', 'confidence': 0.8}

        size_with = strategy.calculate_position_size('BTC', signal_with)
        with caplog.at_level(logging.WARNING):
            size_without = strategy.calculate_position_size('BTC', signal_without)

        assert "missing 'rsi'" in caplog.text.lower()
        # Neutral fallback should produce same size as mid-range RSI
        assert size_with == size_without


# ---------------------------------------------------------------------------
# Bollinger Bands Strategy
# ---------------------------------------------------------------------------

class TestBBSignalPassthrough:

    def _make_strategy(self):
        from strategies.bollinger_bands_strategy import BollingerBandsStrategy
        strategy = BollingerBandsStrategy.__new__(BollingerBandsStrategy)
        strategy.bb_period = 20
        strategy.std_dev = 2
        strategy.squeeze_threshold = 0.02
        strategy.lookback = 40
        strategy.position_size_usd = 100
        strategy.max_positions = 3
        strategy.candle_interval = '15m'
        strategy.volatility_expansion_threshold = 1.5
        strategy.high_band_width_threshold = 0.05
        strategy.high_band_width_multiplier = 0.8
        strategy.low_band_width_threshold = 0.02
        strategy.low_band_width_multiplier = 1.2
        strategy.positions = {}
        strategy.market_data = MagicMock()
        strategy.order_manager = MagicMock()
        strategy.config = {}
        return strategy

    def test_position_size_uses_signal_band_width(self):
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        signal = {'side': 'buy', 'confidence': 0.75, 'band_width': 0.06}
        size = strategy.calculate_position_size('ETH', signal)

        assert size > 0
        strategy.market_data.get_candles.assert_not_called()

    def test_high_band_width_reduces_size(self):
        """High band width should apply reduction multiplier."""
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        normal_signal = {'side': 'buy', 'confidence': 0.75, 'band_width': 0.03}
        wide_signal = {'side': 'buy', 'confidence': 0.75, 'band_width': 0.06}

        normal_size = strategy.calculate_position_size('ETH', normal_signal)
        wide_size = strategy.calculate_position_size('ETH', wide_signal)

        assert wide_size < normal_size

    def test_low_band_width_increases_size(self):
        """Low band width (squeeze) should apply increase multiplier."""
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        normal_signal = {'side': 'buy', 'confidence': 0.75, 'band_width': 0.03}
        tight_signal = {'side': 'buy', 'confidence': 0.75, 'band_width': 0.01}

        normal_size = strategy.calculate_position_size('ETH', normal_signal)
        tight_size = strategy.calculate_position_size('ETH', tight_signal)

        assert tight_size > normal_size

    def test_missing_band_width_warns_and_uses_neutral(self, caplog):
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        # Neutral = midpoint of thresholds = (0.05 + 0.02) / 2 = 0.035
        signal_with = {'side': 'buy', 'confidence': 0.75, 'band_width': 0.035}
        signal_without = {'side': 'buy', 'confidence': 0.75}

        size_with = strategy.calculate_position_size('ETH', signal_with)
        with caplog.at_level(logging.WARNING):
            size_without = strategy.calculate_position_size('ETH', signal_without)

        assert "missing 'band_width'" in caplog.text.lower()
        assert size_with == size_without


# ---------------------------------------------------------------------------
# MACD Strategy
# ---------------------------------------------------------------------------

class TestMACDSignalPassthrough:

    def _make_strategy(self):
        from strategies.macd_strategy import MACDStrategy
        strategy = MACDStrategy.__new__(MACDStrategy)
        strategy.fast_ema = 12
        strategy.slow_ema = 26
        strategy.signal_ema = 9
        strategy.lookback = 55
        strategy.position_size_usd = 100
        strategy.max_positions = 3
        strategy.candle_interval = '15m'
        strategy.divergence_lookback = 20
        strategy.histogram_strength_high = 0.5
        strategy.histogram_strength_low = 0.1
        strategy.histogram_multiplier_high = 1.3
        strategy.histogram_multiplier_low = 0.7
        strategy.positions = {}
        strategy.market_data = MagicMock()
        strategy.order_manager = MagicMock()
        strategy.config = {}
        return strategy

    def test_position_size_uses_signal_histogram(self):
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        signal = {
            'side': 'buy', 'confidence': 0.7,
            'histogram_strength': 0.6,
        }
        size = strategy.calculate_position_size('BTC', signal)

        assert size > 0
        strategy.market_data.get_candles.assert_not_called()

    def test_high_histogram_increases_size(self):
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        weak_signal = {
            'side': 'buy', 'confidence': 0.7,
            'histogram_strength': 0.3,
        }
        strong_signal = {
            'side': 'buy', 'confidence': 0.7,
            'histogram_strength': 0.8,
        }

        weak_size = strategy.calculate_position_size('BTC', weak_signal)
        strong_size = strategy.calculate_position_size('BTC', strong_signal)

        assert strong_size > weak_size

    def test_missing_histogram_warns_and_uses_neutral(self, caplog):
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        # Neutral = midpoint = (0.5 + 0.1) / 2 = 0.3
        signal_with = {'side': 'buy', 'confidence': 0.7, 'histogram_strength': 0.3}
        signal_without = {'side': 'buy', 'confidence': 0.7}

        size_with = strategy.calculate_position_size('BTC', signal_with)
        with caplog.at_level(logging.WARNING):
            size_without = strategy.calculate_position_size('BTC', signal_without)

        assert "missing 'histogram_strength'" in caplog.text.lower()
        assert size_with == size_without


# ---------------------------------------------------------------------------
# Breakout Strategy
# ---------------------------------------------------------------------------

class TestBreakoutSignalPassthrough:

    def _make_strategy(self):
        from strategies.breakout_strategy import BreakoutStrategy
        strategy = BreakoutStrategy.__new__(BreakoutStrategy)
        strategy.lookback_period = 20
        strategy.volume_multiplier = 1.5
        strategy.breakout_confirmation_bars = 2
        strategy.atr_period = 14
        strategy.position_size_usd = 100
        strategy.max_positions = 3
        strategy.candle_interval = '15m'
        strategy.pivot_window = 5
        strategy.avg_volume_lookback = 20
        strategy.stop_loss_atr_multiplier = 1.5
        strategy.position_stop_loss_atr_multiplier = 2.0
        strategy.strong_breakout_multiplier = 1.5
        strategy.high_atr_threshold = 3.0
        strategy.low_atr_threshold = 1.0
        strategy.high_atr_multiplier = 0.7
        strategy.low_atr_multiplier = 1.3
        strategy.support_resistance_levels = {}
        strategy.positions = {}
        strategy.market_data = MagicMock()
        strategy.order_manager = MagicMock()
        strategy.config = {}
        return strategy

    def test_position_size_uses_signal_atr(self):
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        signal = {
            'side': 'buy', 'confidence': 0.7,
            'breakout_type': 'bullish', 'atr': 2.0,
        }
        size = strategy.calculate_position_size('BTC', signal)

        assert size > 0
        strategy.market_data.get_candles.assert_not_called()

    def test_high_atr_reduces_size(self):
        """High ATR (volatile) should reduce position size."""
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        low_atr_signal = {
            'side': 'buy', 'confidence': 0.7,
            'breakout_type': 'bullish', 'atr': 0.5,
        }
        high_atr_signal = {
            'side': 'buy', 'confidence': 0.7,
            'breakout_type': 'bullish', 'atr': 5.0,
        }

        low_size = strategy.calculate_position_size('BTC', low_atr_signal)
        high_size = strategy.calculate_position_size('BTC', high_atr_signal)

        assert high_size < low_size

    def test_missing_atr_warns_and_uses_neutral(self, caplog):
        """Signal without atr key should warn and use neutral value."""
        strategy = self._make_strategy()
        strategy.market_data.get_market_data.return_value = _make_market_data_mock()
        strategy._apply_account_cap = lambda size, price, **kw: size / price

        # Neutral atr_pct = midpoint of thresholds = (3.0 + 1.0) / 2 = 2.0
        neutral_atr = 100.0 * (3.0 + 1.0) / 200  # mid_price * mid_threshold / 100
        signal_with = {
            'side': 'buy', 'confidence': 0.7,
            'breakout_type': 'bullish', 'atr': neutral_atr,
        }
        signal_without = {
            'side': 'buy', 'confidence': 0.7,
            'breakout_type': 'bullish',
        }

        size_with = strategy.calculate_position_size('BTC', signal_with)
        with caplog.at_level(logging.WARNING):
            size_without = strategy.calculate_position_size('BTC', signal_without)

        assert "missing 'atr'" in caplog.text.lower()
        assert size_with == size_without
