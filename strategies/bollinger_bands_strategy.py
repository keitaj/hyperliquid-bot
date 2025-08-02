import logging
from typing import Dict, Optional
import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class BollingerBandsStrategy(BaseStrategy):
    def __init__(self, market_data_manager, order_manager, config):
        super().__init__(market_data_manager, order_manager, config)
        self.bb_period = config.get('bb_period', 20)
        self.std_dev = config.get('std_dev', 2)
        self.squeeze_threshold = config.get('squeeze_threshold', 0.02)
        self.lookback = self.bb_period + 20
        self.position_size_usd = config.get('position_size_usd', 100)
        self.max_positions = config.get('max_positions', 3)
        
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
            candles = self.market_data.get_candles(
                coin=coin,
                interval='15m',
                lookback=self.lookback
            )
            
            if len(candles) < self.bb_period + 2:
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
            
            has_position = coin in self.positions and self.positions[coin]['size'] != 0
            
            if prev_close >= prev_lower and current_close < current_lower:
                if not has_position and band_width > self.squeeze_threshold:
                    logger.info(f"BB lower band touch for {coin}: Price={current_close:.2f}, Lower={current_lower:.2f}")
                    return {
                        'side': 'buy',
                        'order_type': 'limit',
                        'post_only': True,
                        'confidence': 0.75
                    }
            
            elif current_close < current_lower * 0.995:
                if not has_position:
                    logger.info(f"BB strong oversold for {coin}: Price={current_close:.2f}, Lower={current_lower:.2f}")
                    return {
                        'side': 'buy',
                        'order_type': 'market',
                        'confidence': 0.85
                    }
                    
            elif prev_close <= prev_upper and current_close > current_upper:
                if has_position and self.positions[coin]['size'] > 0:
                    logger.info(f"BB upper band touch for {coin}: Price={current_close:.2f}, Upper={current_upper:.2f}")
                    return {
                        'side': 'sell',
                        'order_type': 'market',
                        'reduce_only': True,
                        'confidence': 0.8
                    }
            
            elif has_position and self.positions[coin]['size'] > 0:
                if current_close > current_sma and price_position > 0.8:
                    return {
                        'side': 'sell',
                        'order_type': 'limit',
                        'reduce_only': True,
                        'confidence': 0.6
                    }
                    
            if band_width < self.squeeze_threshold:
                volatility_signal = self._detect_volatility_breakout(df)
                if volatility_signal and not has_position:
                    return volatility_signal
                    
            return None
            
        except Exception as e:
            logger.error(f"Error generating BB signals for {coin}: {e}")
            return None
    
    def _detect_volatility_breakout(self, df: pd.DataFrame) -> Optional[Dict]:
        recent_volatility = df['band_width'].iloc[-5:].mean()
        current_volatility = df['band_width'].iloc[-1]
        
        if current_volatility > recent_volatility * 1.5:
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
            if len(self.positions) >= self.max_positions and coin not in self.positions:
                logger.info(f"Max positions reached, skipping {coin}")
                return 0
                
            market_data = self.market_data.get_market_data(coin)
            if not market_data:
                return 0
                
            confidence = signal.get('confidence', 0.5)
            base_size_usd = self.position_size_usd * confidence
            
            candles = self.market_data.get_candles(coin, '15m', 30)
            df = self.calculate_bollinger_bands(candles)
            
            band_width = df['band_width'].iloc[-1]
            if band_width > 0.05:
                base_size_usd *= 0.8
            elif band_width < 0.02:
                base_size_usd *= 1.2
                
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
            
            logger.info(f"Calculated position size for {coin}: {position_size} (Band Width: {band_width:.4f})")
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating position size for {coin}: {e}")
            return 0