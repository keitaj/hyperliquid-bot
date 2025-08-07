import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
from hyperliquid.info import Info
from rate_limiter import api_wrapper

logger = logging.getLogger(__name__)


@dataclass
class MarketData:
    symbol: str
    mid_price: float
    bid: float
    ask: float
    spread: float
    timestamp: datetime


class MarketDataManager:
    def __init__(self, info: Info):
        self.info = info
        self._cache = {}
        self._meta_cache = None
        self._meta_cache_time = None
        
    def get_all_mids(self) -> Dict[str, float]:
        try:
            return api_wrapper.call(self.info.all_mids)
        except Exception as e:
            logger.error(f"Error fetching mid prices: {e}")
            return {}
    
    def get_meta(self) -> Dict:
        """Get meta information including sz_decimals for all assets"""
        try:
            # Cache meta data for 1 hour
            if self._meta_cache and self._meta_cache_time:
                if (datetime.now() - self._meta_cache_time).seconds < 3600:
                    return self._meta_cache
            
            meta = api_wrapper.call(self.info.meta)
            self._meta_cache = meta
            self._meta_cache_time = datetime.now()
            return meta
        except Exception as e:
            logger.error(f"Error fetching meta data: {e}")
            return {}
    
    def get_sz_decimals(self, coin: str) -> int:
        """Get the number of decimal places allowed for order size"""
        try:
            meta = self.get_meta()
            if 'universe' in meta:
                for asset in meta['universe']:
                    if asset['name'] == coin:
                        return asset['szDecimals']
            # Default to 3 if not found
            return 3
        except Exception as e:
            logger.error(f"Error getting sz_decimals for {coin}: {e}")
            return 3
    
    def get_l2_snapshot(self, coin: str) -> Dict:
        try:
            return api_wrapper.call(self.info.l2_snapshot, coin)
        except Exception as e:
            logger.error(f"Error fetching L2 snapshot for {coin}: {e}")
            return {}
    
    def get_market_data(self, coin: str) -> Optional[MarketData]:
        try:
            l2_data = self.get_l2_snapshot(coin)
            if not l2_data or 'levels' not in l2_data:
                return None
                
            levels = l2_data['levels']
            if len(levels) < 2 or not levels[0] or not levels[1]:
                return None
            
            bids = levels[0]
            asks = levels[1]
            
            if not bids or not asks:
                return None
                
            best_bid = float(bids[0]['px'])
            best_ask = float(asks[0]['px'])
            mid_price = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            
            market_data = MarketData(
                symbol=coin,
                mid_price=mid_price,
                bid=best_bid,
                ask=best_ask,
                spread=spread,
                timestamp=datetime.now()
            )
            
            self._cache[coin] = market_data
            return market_data
            
        except Exception as e:
            logger.error(f"Error getting market data for {coin}: {e}")
            return None
    
    def get_candles(self, coin: str, interval: str, lookback: int = 100) -> pd.DataFrame:
        try:
            # Calculate time range
            import time
            end_time = int(time.time() * 1000)  # Current time in milliseconds
            
            # Calculate start time based on interval and lookback
            interval_ms = {
                '1m': 60 * 1000,
                '5m': 5 * 60 * 1000,
                '15m': 15 * 60 * 1000,
                '1h': 60 * 60 * 1000,
                '4h': 4 * 60 * 60 * 1000,
                '1d': 24 * 60 * 60 * 1000
            }.get(interval, 60 * 1000)  # Default to 1m
            
            start_time = end_time - (lookback * interval_ms)
            
            # Use the correct API call format with positional arguments
            candles = api_wrapper.call(
                self.info.candles_snapshot,
                coin,
                interval,
                start_time,
                end_time
            )
            
            if not candles:
                return pd.DataFrame()
            
            df = pd.DataFrame(candles)
            df['timestamp'] = pd.to_datetime(df['t'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            for col in ['o', 'h', 'l', 'c', 'v']:
                if col in df.columns:
                    df[col] = df[col].astype(float)
                    
            df.rename(columns={
                'o': 'open',
                'h': 'high',
                'l': 'low',
                'c': 'close',
                'v': 'volume'
            }, inplace=True)
            
            return df
            
        except Exception as e:
            logger.error(f"Error fetching candles for {coin}: {e}")
            return pd.DataFrame()
    
    def get_funding_rate(self, coin: str) -> Optional[float]:
        try:
            funding_data = api_wrapper.call(self.info.funding_rates)
            if coin in funding_data:
                return float(funding_data[coin])
            return None
        except Exception as e:
            logger.error(f"Error fetching funding rate for {coin}: {e}")
            return None
    
    def get_open_interest(self, coin: str) -> Optional[float]:
        try:
            oi_data = api_wrapper.call(self.info.open_interest)
            if coin in oi_data:
                return float(oi_data[coin])
            return None
        except Exception as e:
            logger.error(f"Error fetching open interest for {coin}: {e}")
            return None