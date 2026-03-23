import logging
from typing import Dict, Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class BollingerBandsStrategy(BaseStrategy):
    """Bollinger Bands mean-reversion and breakout strategy.

    Buys on lower band touches, sells on upper band touches.  Also
    detects volatility expansion breakouts when band width exceeds
    the squeeze threshold.
    """

    def __init__(self, market_data_manager, order_manager, config):
        super().__init__(market_data_manager, order_manager, config)
        self.bb_period = config.get('bb_period', 20)
        self.std_dev = config.get('std_dev', 2)
        self.squeeze_threshold = config.get('squeeze_threshold', 0.02)
        self.lookback = self.bb_period + 20
        self.position_size_usd = config.get('position_size_usd', 100)
        self.max_positions = config.get('max_positions', 3)
        self.candle_interval = config.get('candle_interval', '15m')
        self.volatility_expansion_threshold = config.get('volatility_expansion_threshold', 1.5)
        self.high_band_width_threshold = config.get('high_band_width_threshold', 0.05)
        self.high_band_width_multiplier = config.get('high_band_width_multiplier', 0.8)
        self.low_band_width_threshold = config.get('low_band_width_threshold', 0.02)
        self.low_band_width_multiplier = config.get('low_band_width_multiplier', 1.2)

    def calculate_bollinger_bands(self, df: pd.DataFrame) -> pd.DataFrame:
        df['sma'] = df['close'].rolling(window=self.bb_period).mean()
        df['std'] = df['close'].rolling(window=self.bb_period).std()
        df['upper_band'] = df['sma'] + (df['std'] * self.std_dev)
        df['lower_band'] = df['sma'] - (df['std'] * self.std_dev)
        df['band_width'] = (df['upper_band'] - df['lower_band']) / df['sma']
        df['price_position'] = (df['close'] - df['lower_band']) / (df['upper_band'] - df['lower_band'])
        return df

    def generate_signals(self, coin: str) -> Optional[Dict]:
        try:
            candles = self._get_candles_or_none(coin, self.bb_period + 2)
            if candles is None:
                return None

            df = self.calculate_bollinger_bands(candles)

            current_close = df['close'].iloc[-1]
            current_upper = df['upper_band'].iloc[-1]
            current_lower = df['lower_band'].iloc[-1]
            current_sma = df['sma'].iloc[-1]
            band_width = df['band_width'].iloc[-1]
            price_position = df['price_position'].iloc[-1]

            prev_close = df['close'].iloc[-2]
            prev_upper = df['upper_band'].iloc[-2]
            prev_lower = df['lower_band'].iloc[-2]

            has_position = self._has_position(coin)

            if prev_close >= prev_lower and current_close < current_lower:
                if not has_position and band_width > self.squeeze_threshold:
                    logger.info(f"BB lower band touch for {coin}: Price={current_close:.2f}, Lower={current_lower:.2f}")
                    return {
                        'side': 'buy',
                        'order_type': 'limit',
                        'post_only': True,
                        'confidence': 0.75,
                        'band_width': band_width,
                    }

            elif current_close < current_lower * 0.995:
                if not self._has_position(coin):
                    logger.info(f"BB strong oversold for {coin}: Price={current_close:.2f}, Lower={current_lower:.2f}")
                    return {
                        'side': 'buy',
                        'order_type': 'market',
                        'confidence': 0.85,
                        'band_width': band_width,
                    }

            elif prev_close <= prev_upper and current_close > current_upper:
                if self._has_position(coin) and self.positions[coin]['size'] > 0:
                    logger.info(f"BB upper band touch for {coin}: Price={current_close:.2f}, Upper={current_upper:.2f}")
                    return {
                        'side': 'sell',
                        'order_type': 'market',
                        'reduce_only': True,
                        'confidence': 0.8,
                        'band_width': band_width,
                    }

            elif self._has_position(coin) and self.positions[coin]['size'] > 0:
                if current_close > current_sma and price_position > 0.8:
                    return {
                        'side': 'sell',
                        'order_type': 'limit',
                        'reduce_only': True,
                        'confidence': 0.6,
                        'band_width': band_width,
                    }

            if band_width < self.squeeze_threshold:
                volatility_signal = self._detect_volatility_breakout(df)
                if volatility_signal and not self._has_position(coin):
                    volatility_signal['band_width'] = band_width
                    return volatility_signal

            return None

        except Exception as e:
            logger.error(f"Error generating BB signals for {coin}: {e}")
            return None

    def _detect_volatility_breakout(self, df: pd.DataFrame) -> Optional[Dict]:
        recent_volatility = df['band_width'].iloc[-5:].mean()
        current_volatility = df['band_width'].iloc[-1]

        if current_volatility > recent_volatility * self.volatility_expansion_threshold:
            price_movement = df['close'].iloc[-1] - df['close'].iloc[-2]
            if price_movement > 0:
                logger.info("Volatility expansion detected - bullish breakout")
                return {
                    'side': 'buy',
                    'order_type': 'market',
                    'confidence': 0.7
                }
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

            band_width = signal.get('band_width', 0.03)

            if band_width > self.high_band_width_threshold:
                base_size_usd *= self.high_band_width_multiplier
            elif band_width < self.low_band_width_threshold:
                base_size_usd *= self.low_band_width_multiplier

            position_size = self._apply_account_cap(base_size_usd, market_data.mid_price)

            logger.info(f"Calculated position size for {coin}: {position_size} (Band Width: {band_width:.4f})")
            return position_size

        except Exception as e:
            logger.error(f"Error calculating position size for {coin}: {e}")
            return 0
