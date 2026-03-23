import logging
from typing import Dict, Optional
import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class BreakoutStrategy(BaseStrategy):
    """Support/resistance breakout strategy.

    Identifies key support and resistance levels via pivot detection,
    then enters on confirmed breakouts with volume confirmation.
    Uses ATR-based stop losses and dynamic position sizing.
    """

    def __init__(self, market_data_manager, order_manager, config):
        super().__init__(market_data_manager, order_manager, config)
        self.lookback_period = config.get('lookback_period', 20)
        self.volume_multiplier = config.get('volume_multiplier', 1.5)
        self.breakout_confirmation_bars = config.get('breakout_confirmation_bars', 2)
        self.atr_period = config.get('atr_period', 14)
        self.position_size_usd = config.get('position_size_usd', 100)
        self.max_positions = config.get('max_positions', 3)
        self.candle_interval = config.get('candle_interval', '15m')
        self.pivot_window = config.get('pivot_window', 5)
        self.avg_volume_lookback = config.get('avg_volume_lookback', 20)
        self.stop_loss_atr_multiplier = config.get('stop_loss_atr_multiplier', 1.5)
        self.position_stop_loss_atr_multiplier = config.get('position_stop_loss_atr_multiplier', 2.0)
        self.strong_breakout_multiplier = config.get('strong_breakout_multiplier', 1.5)
        self.high_atr_threshold = config.get('high_atr_threshold', 3.0)
        self.low_atr_threshold = config.get('low_atr_threshold', 1.0)
        self.high_atr_multiplier = config.get('high_atr_multiplier', 0.7)
        self.low_atr_multiplier = config.get('low_atr_multiplier', 1.3)
        self.support_resistance_levels = {}

    def calculate_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())

        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['atr'] = true_range.rolling(self.atr_period).mean()
        return df

    def identify_support_resistance(self, df: pd.DataFrame) -> Dict:
        highs = df['high'].rolling(window=self.lookback_period).max()
        lows = df['low'].rolling(window=self.lookback_period).min()

        current_resistance = highs.iloc[-1]
        current_support = lows.iloc[-1]

        pivot_highs = []
        pivot_lows = []

        pw = self.pivot_window
        for i in range(pw, len(df) - pw):
            if df['high'].iloc[i] == max(df['high'].iloc[i-pw:i+pw]):
                pivot_highs.append(df['high'].iloc[i])
            if df['low'].iloc[i] == min(df['low'].iloc[i-pw:i+pw]):
                pivot_lows.append(df['low'].iloc[i])

        strong_resistance = None
        strong_support = None

        if pivot_highs:
            resistance_counts = {}
            for high in pivot_highs:
                rounded = round(high, 2)
                resistance_counts[rounded] = resistance_counts.get(rounded, 0) + 1
            strong_resistance = max(resistance_counts, key=resistance_counts.get)

        if pivot_lows:
            support_counts = {}
            for low in pivot_lows:
                rounded = round(low, 2)
                support_counts[rounded] = support_counts.get(rounded, 0) + 1
            strong_support = max(support_counts, key=support_counts.get)

        return {
            'resistance': current_resistance,
            'support': current_support,
            'strong_resistance': strong_resistance,
            'strong_support': strong_support
        }

    def detect_breakout(self, df: pd.DataFrame, levels: Dict) -> Optional[str]:
        recent_bars = df.iloc[-self.breakout_confirmation_bars:]
        current_close = df['close'].iloc[-1]
        current_volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].iloc[-self.avg_volume_lookback:].mean()

        if current_volume < avg_volume * self.volume_multiplier:
            return None

        if levels['resistance'] and all(recent_bars['close'] > levels['resistance']):
            if levels['strong_resistance'] and current_close > levels['strong_resistance']:
                return 'strong_bullish'
            return 'bullish'

        if levels['support'] and all(recent_bars['close'] < levels['support']):
            if levels['strong_support'] and current_close < levels['strong_support']:
                return 'strong_bearish'
            return 'bearish'

        return None

    def generate_signals(self, coin: str) -> Optional[Dict]:
        try:
            candles = self._get_candles_or_none(
                coin,
                self.lookback_period + self.atr_period,
                lookback=max(self.lookback_period * 2, 50),
            )
            if candles is None:
                return None

            df = self.calculate_atr(candles)
            levels = self.identify_support_resistance(df)

            if coin not in self.support_resistance_levels:
                self.support_resistance_levels[coin] = levels
            else:
                self.support_resistance_levels[coin].update(levels)

            breakout_type = self.detect_breakout(df, levels)
            atr = df['atr'].iloc[-1]

            has_position = self._has_position(coin)

            if breakout_type in ['bullish', 'strong_bullish'] and not has_position:
                confidence = 0.7 if breakout_type == 'bullish' else 0.85
                logger.info(f"{breakout_type.upper()} breakout detected for {coin} above {levels['resistance']:.2f}")
                return {
                    'side': 'buy',
                    'order_type': 'market',
                    'confidence': confidence,
                    'breakout_type': breakout_type,
                    'atr': atr,
                    'stop_loss': levels['resistance'] - (atr * self.stop_loss_atr_multiplier),
                }

            elif (breakout_type in ['bearish', 'strong_bearish']
                  and self._has_position(coin) and self.positions[coin]['size'] > 0):
                confidence = 0.75 if breakout_type == 'bearish' else 0.9
                logger.info(f"{breakout_type.upper()} breakout detected for {coin} below {levels['support']:.2f}")
                return {
                    'side': 'sell',
                    'order_type': 'market',
                    'reduce_only': True,
                    'confidence': confidence,
                    'breakout_type': breakout_type,
                    'atr': atr,
                }

            if self._has_position(coin) and self.positions[coin]['size'] > 0:
                entry_price = self.positions[coin]['entry_price']
                current_price = df['close'].iloc[-1]

                if current_price < entry_price - (atr * self.position_stop_loss_atr_multiplier):
                    logger.info(f"Stop loss triggered for {coin}")
                    return {
                        'side': 'sell',
                        'order_type': 'market',
                        'reduce_only': True,
                        'confidence': 1.0,
                        'atr': atr,
                    }

            return None

        except Exception as e:
            logger.error(f"Error generating breakout signals for {coin}: {e}")
            return None

    def calculate_position_size(self, coin: str, signal: Dict) -> float:
        try:
            if self._check_max_positions(coin):
                return 0

            market_data = self.market_data.get_market_data(coin)
            if not market_data:
                return 0

            confidence = signal.get('confidence', 0.5)
            base_size_usd = self.position_size_usd * confidence

            if signal.get('breakout_type') == 'strong_bullish':
                base_size_usd *= self.strong_breakout_multiplier
            elif signal.get('breakout_type') == 'strong_bearish':
                base_size_usd *= (1.0 / self.strong_breakout_multiplier)

            atr = signal.get('atr')
            if atr is None:
                logger.warning("Signal missing 'atr', skipping dynamic sizing")
                atr = market_data.mid_price * (self.high_atr_threshold + self.low_atr_threshold) / 200
            atr_pct = (atr / market_data.mid_price) * 100

            if atr_pct > self.high_atr_threshold:
                base_size_usd *= self.high_atr_multiplier
            elif atr_pct < self.low_atr_threshold:
                base_size_usd *= self.low_atr_multiplier

            position_size = self._apply_account_cap(base_size_usd, market_data.mid_price)

            logger.info(f"Calculated position size for {coin}: {position_size} (ATR: {atr_pct:.2f}%)")
            return position_size

        except Exception as e:
            logger.error(f"Error calculating position size for {coin}: {e}")
            return 0
