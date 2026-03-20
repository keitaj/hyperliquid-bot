"""
Multi-DEX Market Data Manager
Extends MarketDataManager with HIP-3 DEX-aware queries.

Key differences vs standard HL:
- Coin format: "dex:coin" (e.g. "xyz:XYZ100") for HIP-3 assets
- DEX parameter: required for positions/orders on HIP-3 DEXes
- sz_decimals: resolved from DEX registry for HIP-3 assets
"""
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import requests

from hip3.dex_registry import DEXRegistry
from market_data import MarketData, MarketDataManager

logger = logging.getLogger(__name__)


class MultiDexMarketData(MarketDataManager):
    """
    Extends MarketDataManager with HIP-3 multi-DEX support.

    The Hyperliquid API natively accepts "dex:coin" strings in most info
    endpoints (l2Book, candleSnapshot), so standard HL methods work
    transparently for HIP-3 coins.  The main additions are:
      - DEX-scoped position / order queries (require explicit dex param)
      - sz_decimals lookup via registry for HIP-3 coins
    """

    def __init__(self, info, registry: DEXRegistry, api_url: str):
        super().__init__(info)
        self.registry = registry
        self.api_url = api_url.rstrip("/")
        self._hip3_meta_cache: Dict[str, Dict] = {}
        self._hip3_meta_cache_ts: Dict[str, datetime] = {}

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _post(self, payload: dict) -> any:
        resp = requests.post(
            f"{self.api_url}/info",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    # Overrides for HIP-3 support
    # ------------------------------------------------------------------ #

    def get_sz_decimals(self, coin: str) -> int:
        """Returns size decimal precision.  Handles "dex:coin" format."""
        if self.registry.is_hip3(coin):
            dex, coin_name = self.registry.parse_coin(coin)
            return self.registry.get_sz_decimals(dex, coin_name)
        return super().get_sz_decimals(coin)

    def get_meta(self, dex: Optional[str] = None) -> Dict:
        """Get meta for standard HL (dex=None) or a specific HIP-3 DEX."""
        if dex is None:
            return super().get_meta()

        # 1-hour cache per DEX
        cached_ts = self._hip3_meta_cache_ts.get(dex)
        if cached_ts and (datetime.now() - cached_ts).seconds < 3600:
            return self._hip3_meta_cache[dex]

        try:
            meta = self._post({"type": "meta", "dex": dex})
            self._hip3_meta_cache[dex] = meta
            self._hip3_meta_cache_ts[dex] = datetime.now()
            return meta
        except Exception as e:
            logger.error(f"Error fetching meta for DEX '{dex}': {e}")
            return {}

    def get_all_mids(self, dex: Optional[str] = None) -> Dict[str, float]:
        """Get mid prices.  Optionally scoped to a HIP-3 DEX."""
        if dex is None:
            return super().get_all_mids()

        try:
            result = self._post({"type": "allMids", "dex": dex})
            return {k: float(v) for k, v in result.items()}
        except Exception as e:
            logger.error(f"Error fetching mids for DEX '{dex}': {e}")
            return {}

    def get_l2_snapshot(self, coin: str) -> Dict:
        """Override to support HIP-3 "dex:coin" format via direct HTTP call."""
        if not self.registry.is_hip3(coin):
            return super().get_l2_snapshot(coin)
        try:
            return self._post({"type": "l2Book", "coin": coin})
        except Exception as e:
            logger.error(f"Error fetching L2 snapshot for '{coin}': {e}")
            return {}

    def get_candles(self, coin: str, interval: str, lookback: int = 100) -> pd.DataFrame:
        """
        Fetch candles.  For HIP-3 coins the SDK's candle_snapshot may not
        pass the dex:coin string correctly, so we use a direct HTTP call.
        """
        if not self.registry.is_hip3(coin):
            return super().get_candles(coin, interval, lookback)

        try:
            end_time = int(time.time() * 1000)
            interval_ms = {
                "1m": 60_000,
                "5m": 300_000,
                "15m": 900_000,
                "1h": 3_600_000,
                "4h": 14_400_000,
                "1d": 86_400_000,
            }.get(interval, 60_000)
            start_time = end_time - lookback * interval_ms

            candles = self._post({
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": start_time,
                    "endTime": end_time,
                },
            })

            if not candles:
                return pd.DataFrame()

            df = pd.DataFrame(candles)
            df["timestamp"] = pd.to_datetime(df["t"], unit="ms")
            df.set_index("timestamp", inplace=True)
            for col in ["o", "h", "l", "c", "v"]:
                if col in df.columns:
                    df[col] = df[col].astype(float)
            df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}, inplace=True)
            return df

        except Exception as e:
            logger.error(f"Error fetching candles for '{coin}': {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------------ #
    # DEX-scoped position / order queries (not in base SDK)
    # ------------------------------------------------------------------ #

    def get_user_state(self, address: str, dex: Optional[str] = None) -> Dict:
        """
        Clearinghouse state for an account.
        Pass dex to scope to a HIP-3 DEX.
        """
        try:
            payload: Dict = {"type": "clearinghouseState", "user": address}
            if dex:
                payload["dex"] = dex
            return self._post(payload)
        except Exception as e:
            logger.error(f"Error fetching user state (dex={dex}): {e}")
            return {}

    def get_open_orders_dex(self, address: str, dex: Optional[str] = None) -> List[Dict]:
        """Open orders for an account, optionally scoped to a HIP-3 DEX."""
        try:
            payload: Dict = {"type": "openOrders", "user": address}
            if dex:
                payload["dex"] = dex
            return self._post(payload)
        except Exception as e:
            logger.error(f"Error fetching open orders (dex={dex}): {e}")
            return []

    def get_asset_contexts(self, dex: Optional[str] = None) -> List[Dict]:
        """
        Asset contexts (mark price, funding rate, OI) for a DEX.
        Returns the second element of metaAndAssetCtxs response.
        """
        try:
            payload: Dict = {"type": "metaAndAssetCtxs"}
            if dex:
                payload["dex"] = dex
            result = self._post(payload)
            # Response: [meta, [assetCtx, ...]]
            if isinstance(result, list) and len(result) == 2:
                return result[1]
            return []
        except Exception as e:
            logger.error(f"Error fetching asset contexts (dex={dex}): {e}")
            return []
