import logging
from typing import Dict, Optional
import pandas as pd
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class SimpleMAStrategy(BaseStrategy):
    def __init__(self, market_data_manager, order_manager, config):
        super().__init__(market_data_manager, order_manager, config)
        self.fast_period = config.get('fast_ma_period', 10)
        self.slow_period = config.get('slow_ma_period', 30)
        self.lookback = max(self.fast_period, self.slow_period) + 10
        self.position_size_usd = config.get('position_size_usd', 100)
        self.max_positions = config.get('max_positions', 3)
        
    def calculate_moving_averages(self, df: pd.DataFrame) -> pd.DataFrame:
        df['ma_fast'] = df['close'].rolling(window=self.fast_period).mean()
        df['ma_slow'] = df['close'].rolling(window=self.slow_period).mean()
        return df
    
    def generate_signals(self, coin: str) -> Optional[Dict]:
        try:
            candles = self.market_data.get_candles(
                coin=coin,
                interval='5m',
                lookback=self.lookback
            )
            
            if len(candles) < self.slow_period:
                return None
                
            df = self.calculate_moving_averages(candles)
            
            current_fast_ma = df['ma_fast'].iloc[-1]
            current_slow_ma = df['ma_slow'].iloc[-1]
            prev_fast_ma = df['ma_fast'].iloc[-2]
            prev_slow_ma = df['ma_slow'].iloc[-2]
            
            has_position = coin in self.positions and self.positions[coin]['size'] != 0
            
            if prev_fast_ma <= prev_slow_ma and current_fast_ma > current_slow_ma:
                if not has_position:
                    logger.info(f"Bullish crossover detected for {coin}")
                    return {
                        'side': 'buy',
                        'order_type': 'limit',
                        'post_only': True,
                        'confidence': 0.7
                    }
                    
            elif prev_fast_ma >= prev_slow_ma and current_fast_ma < current_slow_ma:
                if has_position and self.positions[coin]['size'] > 0:
                    logger.info(f"Bearish crossover detected for {coin}")
                    return {
                        'side': 'sell',
                        'order_type': 'market',
                        'reduce_only': True,
                        'confidence': 0.8
                    }
                    
            return None
            
        except Exception as e:
            logger.error(f"Error generating signals for {coin}: {e}")
            return None
    
    def calculate_position_size(self, coin: str, signal: Dict) -> float:
        try:
            if len(self.positions) >= self.max_positions and coin not in self.positions:
                logger.info(f"Max positions reached, skipping {coin}")
                return 0
                
            market_data = self.market_data.get_market_data(coin)
            if not market_data:
                return 0
                
            confidence = signal.get('confidence', 0.5)
            base_size_usd = self.position_size_usd * confidence
            
            position_size = base_size_usd / market_data.mid_price
            
            user_state = self.order_manager.info.user_state(
                self.order_manager.account_address
            )
            
            if 'marginSummary' in user_state:
                account_value = float(user_state['marginSummary']['accountValue'])
                max_size_usd = account_value * 0.1
                
                if base_size_usd > max_size_usd:
                    position_size = max_size_usd / market_data.mid_price
                    
            position_size = round(position_size, 4)
            
            logger.info(f"Calculated position size for {coin}: {position_size}")
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating position size for {coin}: {e}")
            return 0