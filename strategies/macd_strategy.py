import logging
from typing import Dict, Optional
import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class MACDStrategy(BaseStrategy):
    def __init__(self, market_data_manager, order_manager, config):
        super().__init__(market_data_manager, order_manager, config)
        self.fast_ema = config.get('fast_ema', 12)
        self.slow_ema = config.get('slow_ema', 26)
        self.signal_ema = config.get('signal_ema', 9)
        self.lookback = max(self.slow_ema, self.fast_ema) + self.signal_ema + 20
        self.position_size_usd = config.get('position_size_usd', 100)
        self.max_positions = config.get('max_positions', 3)
        
    def calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        df['ema_fast'] = df['close'].ewm(span=self.fast_ema, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.slow_ema, adjust=False).mean()
        df['macd_line'] = df['ema_fast'] - df['ema_slow']
        df['signal_line'] = df['macd_line'].ewm(span=self.signal_ema, adjust=False).mean()
        df['macd_histogram'] = df['macd_line'] - df['signal_line']
        
        df['macd_pct'] = (df['macd_line'] / df['close']) * 100
        df['histogram_pct'] = (df['macd_histogram'] / df['close']) * 100
        
        return df
    
    def detect_divergence(self, df: pd.DataFrame, lookback: int = 20) -> Dict:
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
            candles = self.market_data.get_candles(
                coin=coin,
                interval='15m',
                lookback=self.lookback
            )
            
            if len(candles) < self.lookback:
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
            
            divergence = self.detect_divergence(df)
            
            has_position = coin in self.positions and self.positions[coin]['size'] != 0
            
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
                        'confidence': confidence
                    }
                    
            elif not has_position and divergence['bullish_divergence'] and histogram_increasing:
                logger.info(f"MACD bullish divergence signal for {coin}")
                return {
                    'side': 'buy',
                    'order_type': 'limit',
                    'post_only': True,
                    'confidence': 0.75
                }
                    
            elif prev_macd >= prev_signal and current_macd < current_signal:
                if has_position and self.positions[coin]['size'] > 0:
                    logger.info(f"MACD bearish crossover for {coin}: MACD={current_macd:.4f}")
                    confidence = 0.75
                    if divergence['bearish_divergence']:
                        confidence = 0.9
                        logger.info(f"Bearish divergence detected for {coin}")
                    return {
                        'side': 'sell',
                        'order_type': 'market',
                        'reduce_only': True,
                        'confidence': confidence
                    }
            
            elif has_position and self.positions[coin]['size'] > 0:
                if not histogram_positive and not histogram_increasing:
                    return {
                        'side': 'sell',
                        'order_type': 'limit',
                        'reduce_only': True,
                        'confidence': 0.6
                    }
                    
            return None
            
        except Exception as e:
            logger.error(f"Error generating MACD signals for {coin}: {e}")
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
            
            candles = self.market_data.get_candles(coin, '15m', 50)
            df = self.calculate_macd(candles)
            
            histogram_strength = abs(df['histogram_pct'].iloc[-1])
            if histogram_strength > 0.5:
                base_size_usd *= 1.3
            elif histogram_strength < 0.1:
                base_size_usd *= 0.7
                
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
            
            logger.info(f"Calculated position size for {coin}: {position_size} (Histogram: {histogram_strength:.4f}%)")
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating position size for {coin}: {e}")
            return 0