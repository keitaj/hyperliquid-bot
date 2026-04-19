import logging
from typing import Dict, Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy
from rate_limiter import API_ERRORS

logger = logging.getLogger(__name__)


class MACDStrategy(BaseStrategy):
    """MACD crossover strategy with divergence detection.

    Generates buy signals on bullish MACD/signal crossovers and sell
    signals on bearish crossovers.  Confidence is boosted when price
    divergence is detected against the histogram.
    """

    def __init__(self, market_data_manager, order_manager, config):
        super().__init__(market_data_manager, order_manager, config)
        self.fast_ema = config.get('fast_ema', 12)
        self.slow_ema = config.get('slow_ema', 26)
        self.signal_ema = config.get('signal_ema', 9)
        self.lookback = max(self.slow_ema, self.fast_ema) + self.signal_ema + 20
        self.position_size_usd = config.get('position_size_usd', 100)
        self.max_positions = config.get('max_positions', 3)
        self.candle_interval = config.get('candle_interval', '15m')
        self.divergence_lookback = config.get('divergence_lookback', 20)
        self.histogram_strength_high = config.get('histogram_strength_high', 0.5)
        self.histogram_strength_low = config.get('histogram_strength_low', 0.1)
        self.histogram_multiplier_high = config.get('histogram_multiplier_high', 1.3)
        self.histogram_multiplier_low = config.get('histogram_multiplier_low', 0.7)

    def calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        df['ema_fast'] = df['close'].ewm(span=self.fast_ema, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.slow_ema, adjust=False).mean()
        df['macd_line'] = df['ema_fast'] - df['ema_slow']
        df['signal_line'] = df['macd_line'].ewm(span=self.signal_ema, adjust=False).mean()
        df['macd_histogram'] = df['macd_line'] - df['signal_line']

        df['macd_pct'] = (df['macd_line'] / df['close']) * 100
        df['histogram_pct'] = (df['macd_histogram'] / df['close']) * 100

        return df

    def detect_divergence(self, df: pd.DataFrame, lookback: int = None) -> Dict:
        if lookback is None:
            lookback = self.divergence_lookback
        recent_df = df.iloc[-lookback:]

        price_highs_idx = recent_df['high'].nlargest(2).index
        price_lows_idx = recent_df['low'].nsmallest(2).index

        bullish_divergence = False
        bearish_divergence = False

        if len(price_lows_idx) >= 2:
            idx1, idx2 = sorted(price_lows_idx)
            if (recent_df.loc[idx2, 'low'] < recent_df.loc[idx1, 'low'] and
                    recent_df.loc[idx2, 'macd_histogram'] > recent_df.loc[idx1, 'macd_histogram']):
                bullish_divergence = True

        if len(price_highs_idx) >= 2:
            idx1, idx2 = sorted(price_highs_idx)
            if (recent_df.loc[idx2, 'high'] > recent_df.loc[idx1, 'high'] and
                    recent_df.loc[idx2, 'macd_histogram'] < recent_df.loc[idx1, 'macd_histogram']):
                bearish_divergence = True

        return {
            'bullish_divergence': bullish_divergence,
            'bearish_divergence': bearish_divergence
        }

    def generate_signals(self, coin: str) -> Optional[Dict]:
        try:
            candles = self._get_candles_or_none(coin, self.lookback)
            if candles is None:
                return None

            df = self.calculate_macd(candles)

            current_macd = df['macd_line'].iloc[-1]
            current_signal = df['signal_line'].iloc[-1]
            current_histogram = df['macd_histogram'].iloc[-1]

            prev_macd = df['macd_line'].iloc[-2]
            prev_signal = df['signal_line'].iloc[-2]
            prev_histogram = df['macd_histogram'].iloc[-2]

            histogram_increasing = current_histogram > prev_histogram
            histogram_positive = current_histogram > 0

            histogram_strength = abs(df['histogram_pct'].iloc[-1])
            divergence = self.detect_divergence(df)

            has_position = self._has_position(coin)

            if prev_macd <= prev_signal and current_macd > current_signal:
                if not has_position and current_macd < 0:
                    logger.info(f"MACD bullish crossover for {coin}: MACD={current_macd:.4f}")
                    confidence = 0.7
                    if divergence['bullish_divergence']:
                        confidence = 0.85
                        logger.info(f"Bullish divergence detected for {coin}")
                    return {
                        'side': 'buy',
                        'order_type': 'limit',
                        'post_only': True,
                        'confidence': confidence,
                        'histogram_strength': histogram_strength,
                    }

            elif not self._has_position(coin) and divergence['bullish_divergence'] and histogram_increasing:
                logger.info(f"MACD bullish divergence signal for {coin}")
                return {
                    'side': 'buy',
                    'order_type': 'limit',
                    'post_only': True,
                    'confidence': 0.75,
                    'histogram_strength': histogram_strength,
                }

            elif prev_macd >= prev_signal and current_macd < current_signal:
                if self._has_position(coin) and self.positions[coin]['size'] > 0:
                    logger.info(f"MACD bearish crossover for {coin}: MACD={current_macd:.4f}")
                    confidence = 0.75
                    if divergence['bearish_divergence']:
                        confidence = 0.9
                        logger.info(f"Bearish divergence detected for {coin}")
                    return {
                        'side': 'sell',
                        'order_type': 'market',
                        'reduce_only': True,
                        'confidence': confidence,
                        'histogram_strength': histogram_strength,
                    }

            elif self._has_position(coin) and self.positions[coin]['size'] > 0:
                if not histogram_positive and not histogram_increasing:
                    return {
                        'side': 'sell',
                        'order_type': 'limit',
                        'reduce_only': True,
                        'confidence': 0.6,
                        'histogram_strength': histogram_strength,
                    }

            return None

        except API_ERRORS as e:
            logger.error(f"Error generating MACD signals for {coin}: {e}")
            return None

    def _adjust_size_usd(self, base_size_usd: float, signal: Dict,
                         market_data) -> float:
        histogram_strength = signal.get('histogram_strength')
        if histogram_strength is None:
            logger.warning("Signal missing 'histogram_strength', skipping dynamic sizing")
            return base_size_usd

        if histogram_strength > self.histogram_strength_high:
            base_size_usd *= self.histogram_multiplier_high
        elif histogram_strength < self.histogram_strength_low:
            base_size_usd *= self.histogram_multiplier_low

        return base_size_usd

    def _size_log_detail(self, signal: Dict) -> str:
        hs = signal.get('histogram_strength')
        return f" (Histogram: {hs:.4f}%)" if hs is not None else ""
