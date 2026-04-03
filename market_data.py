import logging
import time
from typing import Dict, Optional
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
from hyperliquid.info import Info
from rate_limiter import api_wrapper, API_ERRORS

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
    def __init__(self, info: Info, meta_cache_ttl: float = 3600,
                 market_data_cache_ttl: float = 2.0):
        self.info = info
        self._cache: Dict[str, MarketData] = {}
        self._cache_time: Dict[str, float] = {}
        self._cache_ttl = market_data_cache_ttl
        self._meta_cache = None
        self._meta_cache_time: float = 0.0
        self._meta_cache_ttl = meta_cache_ttl

    def get_all_mids(self) -> Dict[str, float]:
        try:
            return api_wrapper.call(self.info.all_mids)
        except API_ERRORS as e:
            logger.error(f"Error fetching mid prices: {e}")
            return {}

    def get_meta(self) -> Dict:
        """Get meta information including sz_decimals for all assets"""
        try:
            now = time.monotonic()
            if self._meta_cache and (now - self._meta_cache_time) < self._meta_cache_ttl:
                return self._meta_cache

            meta = api_wrapper.call(self.info.meta)
            self._meta_cache = meta
            self._meta_cache_time = now
            return meta
        except API_ERRORS as e:
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
            logger.warning(f"sz_decimals not found for {coin}, using default=3")
            return 3
        except API_ERRORS as e:
            logger.error(f"Error getting sz_decimals for {coin} (using default=3): {e}")
            return 3

    def round_size(self, coin: str, size: float) -> float:
        """Round *size* to the sz_decimals precision for *coin*."""
        return round(size, self.get_sz_decimals(coin))

    def get_l2_snapshot(self, coin: str) -> Dict:
        try:
            return api_wrapper.call(self.info.l2_snapshot, coin)
        except API_ERRORS as e:
            logger.error(f"Error fetching L2 snapshot for {coin}: {e}")
            return {}

    def get_market_data(self, coin: str) -> Optional[MarketData]:
        try:
            # Return cached data if still fresh
            now = time.monotonic()
            cached_time = self._cache_time.get(coin, 0.0)
            if now - cached_time < self._cache_ttl and coin in self._cache:
                return self._cache[coin]

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
            self._cache_time[coin] = now
            return market_data

        except API_ERRORS as e:
            logger.error(f"Error getting market data for {coin}: {e}")
            return None

    def get_candles(self, coin: str, interval: str, lookback: int = 100) -> pd.DataFrame:
        try:
            # Calculate time range
            end_time = int(time.time() * 1000)  # Current time in milliseconds

            # Calculate start time based on interval and lookback
            _INTERVAL_MS = {
                '1m': 60_000,
                '3m': 3 * 60_000,
                '5m': 5 * 60_000,
                '15m': 15 * 60_000,
                '30m': 30 * 60_000,
                '1h': 3_600_000,
                '2h': 2 * 3_600_000,
                '4h': 4 * 3_600_000,
                '12h': 12 * 3_600_000,
                '1d': 86_400_000,
                '1w': 7 * 86_400_000,
                '1M': 30 * 86_400_000,
            }
            interval_ms = _INTERVAL_MS.get(interval)
            if interval_ms is None:
                logger.warning(
                    "Unknown candle interval '%s', falling back to 1m", interval
                )
                interval_ms = 60_000

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

        except API_ERRORS as e:
            logger.error(f"Error fetching candles for {coin} (interval={interval}, lookback={lookback}): {e}")
            return pd.DataFrame()

    def get_funding_rate(self, coin: str) -> Optional[float]:
        try:
            funding_data = api_wrapper.call(self.info.funding_rates)
            if coin in funding_data:
                return float(funding_data[coin])
            return None
        except API_ERRORS as e:
            logger.error(f"Error fetching funding rate for {coin}: {e}")
            return None

    def get_open_interest(self, coin: str) -> Optional[float]:
        try:
            oi_data = api_wrapper.call(self.info.open_interest)
            if coin in oi_data:
                return float(oi_data[coin])
            return None
        except API_ERRORS as e:
            logger.error(f"Error fetching open interest for {coin}: {e}")
            return None
