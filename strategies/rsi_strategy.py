import logging
from typing import Dict, Optional
import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class RSIStrategy(BaseStrategy):
    def __init__(self, market_data_manager, order_manager, config):
        super().__init__(market_data_manager, order_manager, config)
        self.rsi_period = config.get('rsi_period', 14)
        self.oversold_threshold = config.get('oversold_threshold', 30)
        self.overbought_threshold = config.get('overbought_threshold', 70)
        self.lookback = self.rsi_period + 20
        self.position_size_usd = config.get('position_size_usd', 100)
        self.max_positions = config.get('max_positions', 3)
        self.candle_interval = config.get('candle_interval', '15m')
        self.rsi_extreme_low = config.get('rsi_extreme_low', 25)
        self.rsi_moderate_low = config.get('rsi_moderate_low', 35)
        self.size_multiplier_extreme = config.get('size_multiplier_extreme', 1.5)
        self.size_multiplier_moderate = config.get('size_multiplier_moderate', 1.2)
        
    def calculate_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        return df
    
    def generate_signals(self, coin: str) -> Optional[Dict]:
        try:
            candles = self.market_data.get_candles(
                coin=coin,
                interval=self.candle_interval,
                lookback=self.lookback
            )
            
            if len(candles) < self.rsi_period + 2:
                return None
                
            df = self.calculate_rsi(candles)
            
            current_rsi = df['rsi'].iloc[-1]
            prev_rsi = df['rsi'].iloc[-2]
            
            has_position = coin in self.positions and self.positions[coin]['size'] != 0
            
            if prev_rsi >= self.oversold_threshold and current_rsi < self.oversold_threshold:
                if not has_position:
                    logger.info(f"RSI oversold signal for {coin}: RSI={current_rsi:.2f}")
                    return {
                        'side': 'buy',
                        'order_type': 'limit',
                        'post_only': True,
                        'confidence': 0.8
                    }
                    
            elif prev_rsi <= self.overbought_threshold and current_rsi > self.overbought_threshold:
                if has_position and self.positions[coin]['size'] > 0:
                    logger.info(f"RSI overbought signal for {coin}: RSI={current_rsi:.2f}")
                    return {
                        'side': 'sell',
                        'order_type': 'market',
                        'reduce_only': True,
                        'confidence': 0.8
                    }
            
            elif has_position and self.positions[coin]['size'] > 0:
                if current_rsi > self.overbought_threshold - 5:
                    return {
                        'side': 'sell',
                        'order_type': 'limit',
                        'reduce_only': True,
                        'confidence': 0.6
                    }
                    
            return None
            
        except Exception as e:
            logger.error(f"Error generating RSI signals for {coin}: {e}")
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

            candles = self.market_data.get_candles(coin, '15m', 20)
            df = self.calculate_rsi(candles)
            current_rsi = df['rsi'].iloc[-1]

            if signal['side'] == 'buy' and current_rsi < self.rsi_extreme_low:
                base_size_usd *= self.size_multiplier_extreme
            elif signal['side'] == 'buy' and current_rsi < self.rsi_moderate_low:
                base_size_usd *= self.size_multiplier_moderate

            position_size = self._apply_account_cap(base_size_usd, market_data.mid_price)

            logger.info(f"Calculated position size for {coin}: {position_size} (RSI: {current_rsi:.2f})")
            return position_size

        except Exception as e:
            logger.error(f"Error calculating position size for {coin}: {e}")
            return 0