"""
Multi-DEX Market Data Manager
Extends MarketDataManager with HIP-3 DEX-aware queries.

As of hyperliquid-python-sdk 0.22.0, the SDK natively supports HIP-3:
- Info(perp_dexs=["xyz"]) auto-populates coin_to_asset / asset_to_sz_decimals
- l2_snapshot("xyz:GOLD"), candles_snapshot("xyz:GOLD", ...) work transparently
- user_state(address, dex="xyz"), open_orders(address, dex="xyz") native support
- all_mids(dex="xyz"), meta(dex="xyz") native support

This class adds only the thin wrappers that are not on the base MarketDataManager.
"""
import logging
import requests
from typing import Dict, List, Optional, Tuple

from rate_limiter import API_ERRORS
from coin_utils import is_hip3, parse_coin

from hip3.dex_registry import DEXRegistry
from market_data import MarketDataManager
from rate_limiter import api_wrapper
from ttl_cache import TTLCacheMap

logger = logging.getLogger(__name__)


class MultiDexMarketData(MarketDataManager):
    """
    Extends MarketDataManager with HIP-3 multi-DEX support.

    Requires Info to be initialised with perp_dexs so that coin_to_asset
    and asset_to_sz_decimals are already populated for HIP-3 assets.
    """

    def __init__(self, info, registry: DEXRegistry, api_url: str, meta_cache_ttl: float = 3600,
                 user_state_cache_ttl: float = 2.0):
        super().__init__(info, meta_cache_ttl=meta_cache_ttl)
        self.registry = registry
        self.api_url = api_url.rstrip("/")
        # Per-DEX user_state cache keyed by (address, dex)
        self._dex_user_state_cache: TTLCacheMap[Tuple[str, str], Dict] = TTLCacheMap(user_state_cache_ttl)
        self._user_state_cache_ttl = user_state_cache_ttl
        # Per-DEX open orders cache (same TTL as user_state)
        self._dex_open_orders_cache: TTLCacheMap[Tuple[str, str], List] = TTLCacheMap(user_state_cache_ttl)

    # ------------------------------------------------------------------ #
    # Overrides leveraging 0.22.0 SDK native HIP-3 support
    # ------------------------------------------------------------------ #

    def get_sz_decimals(self, coin: str) -> int:
        """Returns size decimal precision. Handles "dex:coin" format via SDK."""
        if is_hip3(coin):
            asset_id = self.info.coin_to_asset.get(coin)
            if asset_id is not None:
                return self.info.asset_to_sz_decimals.get(asset_id, 3)
            # Fallback to registry
            dex, coin_name = parse_coin(coin)
            return self.registry.get_sz_decimals(dex, coin_name)
        return super().get_sz_decimals(coin)

    def get_all_mids(self, dex: Optional[str] = None) -> Dict[str, float]:
        """Get mid prices, optionally scoped to a HIP-3 DEX."""
        try:
            result = self.info.all_mids(dex=dex or "")
            return {k: float(v) for k, v in result.items()}
        except API_ERRORS as e:
            logger.error(f"Error fetching mids (dex={dex}): {e}")
            return {}

    # ------------------------------------------------------------------ #
    # DEX-scoped position / order queries — delegates to SDK in 0.22.0
    # ------------------------------------------------------------------ #

    def get_user_state(self, address: str, dex: Optional[str] = None) -> Dict:
        """Clearinghouse state for an account, optionally scoped to a HIP-3 DEX.

        Results are cached per (address, dex) with a short TTL so that
        multiple components (RiskManager, OrderManager) calling this in the
        same bot cycle share a single API call.
        """
        cache_key = (address, dex or "")
        cached = self._dex_user_state_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            result = self.info.user_state(address, dex=dex or "")
            self._dex_user_state_cache.set(cache_key, result)
            return result
        except API_ERRORS as e:
            logger.error(f"Error fetching user state (dex={dex}): {e}")
            self._dex_user_state_cache.invalidate(cache_key)
            return {}

    def get_open_orders_dex(self, address: str, dex: Optional[str] = None) -> List[Dict]:
        """Open orders for an account, optionally scoped to a HIP-3 DEX.

        Uses a short-lived cache (same TTL as user_state) so that multiple
        callers in the same cycle share a single API call (weight 20 each).
        """
        cache_key = (address, dex or "")
        cached = self._dex_open_orders_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            result = self.info.open_orders(address, dex=dex or "")
            self._dex_open_orders_cache.set(cache_key, result)
            return result
        except API_ERRORS as e:
            logger.error(f"Error fetching open orders (dex={dex}): {e}")
            return []

    def _fetch_user_fills_dex(self, address: str, dex: str) -> List[Dict]:
        """Raw HTTP call for user fills (SDK user_fills has no dex param)."""
        resp = requests.post(
            f"{self.api_url}/info",
            json={"type": "userFills", "user": address, "dex": dex},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_user_fills_dex(self, address: str, dex: str) -> List[Dict]:
        """User fills for a specific HIP-3 DEX, routed through the rate limiter."""
        try:
            return api_wrapper.call(self._fetch_user_fills_dex, address, dex)
        except API_ERRORS as e:
            logger.error(f"Error fetching fills for DEX '{dex}': {e}")
            return []
