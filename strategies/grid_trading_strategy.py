import logging
from typing import Dict, Optional, List
import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class GridTradingStrategy(BaseStrategy):
    def __init__(self, market_data_manager, order_manager, config):
        super().__init__(market_data_manager, order_manager, config)
        self.grid_levels = config.get('grid_levels', 10)
        self.grid_spacing_pct = config.get('grid_spacing_pct', 0.5)
        self.position_size_per_grid = config.get('position_size_per_grid', 50)
        self.max_positions = config.get('max_positions', 5)
        self.range_period = config.get('range_period', 100)
        self.active_grids = {}
        
    def calculate_price_range(self, df: pd.DataFrame) -> Dict:
        high = df['high'].max()
        low = df['low'].min()
        current_price = df['close'].iloc[-1]
        
        range_size = high - low
        range_pct = (range_size / current_price) * 100
        
        volatility = df['close'].pct_change().std() * np.sqrt(len(df))
        
        return {
            'high': high,
            'low': low,
            'current': current_price,
            'range_size': range_size,
            'range_pct': range_pct,
            'volatility': volatility,
            'is_ranging': range_pct < 10 and volatility < 0.15
        }
    
    def calculate_grid_levels(self, price_range: Dict) -> List[float]:
        current_price = price_range['current']
        grid_interval = current_price * (self.grid_spacing_pct / 100)
        
        grid_prices = []
        
        for i in range(self.grid_levels // 2):
            buy_price = current_price - (grid_interval * (i + 1))
            sell_price = current_price + (grid_interval * (i + 1))
            
            if buy_price > price_range['low'] * 0.98:
                grid_prices.append(('buy', buy_price))
            if sell_price < price_range['high'] * 1.02:
                grid_prices.append(('sell', sell_price))
                
        return sorted(grid_prices, key=lambda x: x[1])
    
    def generate_signals(self, coin: str) -> Optional[Dict]:
        try:
            candles = self.market_data.get_candles(
                coin=coin,
                interval='15m',
                lookback=self.range_period
            )
            
            if len(candles) < 50:
                return None
                
            df = pd.DataFrame(candles)
            price_range = self.calculate_price_range(df)
            
            if not price_range['is_ranging']:
                logger.info(f"{coin} not in ranging market, skipping grid strategy")
                return None
                
            current_price = price_range['current']
            
            if coin not in self.active_grids:
                self.active_grids[coin] = {
                    'levels': self.calculate_grid_levels(price_range),
                    'filled_orders': {},
                    'last_update': df.index[-1]
                }
                
            grid_info = self.active_grids[coin]
            
            for order_type, grid_price in grid_info['levels']:
                price_key = f"{order_type}_{grid_price:.2f}"
                
                if price_key in grid_info['filled_orders']:
                    continue
                    
                if order_type == 'buy' and current_price <= grid_price * 1.001:
                    if len(self.positions) < self.max_positions:
                        logger.info(f"Grid buy signal for {coin} at {grid_price:.2f}")
                        grid_info['filled_orders'][price_key] = True
                        return {
                            'side': 'buy',
                            'order_type': 'limit',
                            'post_only': True,
                            'confidence': 0.6,
                            'grid_price': grid_price
                        }
                        
                elif order_type == 'sell' and current_price >= grid_price * 0.999:
                    if coin in self.positions and self.positions[coin]['size'] > 0:
                        logger.info(f"Grid sell signal for {coin} at {grid_price:.2f}")
                        grid_info['filled_orders'][price_key] = True
                        return {
                            'side': 'sell',
                            'order_type': 'limit',
                            'post_only': True,
                            'reduce_only': True,
                            'confidence': 0.6,
                            'grid_price': grid_price
                        }
            
            if len(candles) - candles.index.get_loc(grid_info['last_update']) > 20:
                self.active_grids[coin] = {
                    'levels': self.calculate_grid_levels(price_range),
                    'filled_orders': {},
                    'last_update': df.index[-1]
                }
                logger.info(f"Grid levels recalculated for {coin}")
                
            return None
            
        except Exception as e:
            logger.error(f"Error generating grid signals for {coin}: {e}")
            return None
    
    def _calculate_limit_price(self, market_data, side: str) -> float:
        if hasattr(self, '_current_signal') and 'grid_price' in self._current_signal:
            return self._current_signal['grid_price']
        return super()._calculate_limit_price(market_data, side)
    
    def execute_signal(self, coin: str, signal: Dict):
        self._current_signal = signal
        super().execute_signal(coin, signal)
        self._current_signal = None
    
    def calculate_position_size(self, coin: str, signal: Dict) -> float:
        try:
            market_data = self.market_data.get_market_data(coin)
            if not market_data:
                return 0
                
            base_size_usd = self.position_size_per_grid
            
            if coin in self.active_grids:
                filled_count = len(self.active_grids[coin]['filled_orders'])
                if filled_count > self.grid_levels * 0.7:
                    base_size_usd *= 0.5
                    
            position_size = base_size_usd / market_data.mid_price
            
            user_state = self.order_manager.info.user_state(
                self.order_manager.account_address
            )
            
            if 'marginSummary' in user_state:
                account_value = float(user_state['marginSummary']['accountValue'])
                max_size_usd = account_value * 0.05
                
                if base_size_usd > max_size_usd:
                    position_size = max_size_usd / market_data.mid_price
            
            # Don't round here since BaseStrategy.execute_signal will handle it
            logger.info(f"Grid position size for {coin}: {position_size}")
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating grid position size for {coin}: {e}")
            return 0