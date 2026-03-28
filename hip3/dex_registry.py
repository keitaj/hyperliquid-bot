"""
HIP-3 DEX Registry
Discovers builder-deployed perpetuals DEXes and resolves asset IDs.

Asset ID formula: 100000 + (perp_dex_index * 10000) + index_in_meta
Standard HL perps use index 0..N directly (not HIP-3).
"""
import logging
import requests
from typing import Any, Dict, List, Optional, Tuple
from coin_utils import is_hip3 as _is_hip3, parse_coin as _parse_coin

logger = logging.getLogger(__name__)


class DEXRegistry:
    """
    Manages HIP-3 DEX discovery and asset ID resolution.

    Usage:
        registry = DEXRegistry(api_url)
        registry.discover(["xyz", "flx"])
        asset_id = registry.get_asset_id("xyz", "XYZ100")  # → e.g. 110000
    """

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")
        # dex_name → {perp_dex_index, assets: {coin → {asset_id, sz_decimals}}, meta}
        self._dexes: Dict[str, Dict] = {}

    def _post(self, payload: dict) -> Any:
        resp = requests.post(
            f"{self.api_url}/info",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def discover(self, target_dexes: Optional[List[str]] = None) -> None:
        """
        Discover HIP-3 DEXes from the API and populate registry.

        Args:
            target_dexes: DEX names to register. None = register all found.
        """
        try:
            perp_dexes = self._post({"type": "perpDexs"})
        except Exception as e:
            raise RuntimeError(f"Failed to fetch perpDexs: {e}") from e

        if not isinstance(perp_dexes, list):
            raise RuntimeError(f"Unexpected perpDexs response: {perp_dexes}")

        for i, entry in enumerate(perp_dexes):
            # Index 0 is always null (standard Hyperliquid, not a HIP-3 DEX)
            if entry is None:
                continue
            # API returns list of objects; name may be in "name" or "dex" field
            dex_name = entry.get("name") or entry.get("dex", "")
            if not dex_name:
                continue
            if target_dexes and dex_name not in target_dexes:
                continue

            try:
                meta = self._post({"type": "meta", "dex": dex_name})
                universe = meta.get("universe", [])

                assets: Dict[str, Dict] = {}
                for j, asset in enumerate(universe):
                    coin = asset["name"]
                    # Meta API may return "dex:coin" format; store bare coin name only
                    _, coin = _parse_coin(coin)
                    sz_decimals = asset.get("szDecimals", 3)
                    asset_id = 100000 + i * 10000 + j
                    assets[coin] = {
                        "asset_id": asset_id,
                        "sz_decimals": sz_decimals,
                        "meta_idx": j,
                    }

                self._dexes[dex_name] = {
                    "perp_dex_index": i,
                    "assets": assets,
                    "meta": meta,
                }
                logger.info(
                    f"Registered DEX '{dex_name}' (perp_dex_index={i}) "
                    f"with {len(assets)} assets: {list(assets.keys())}"
                )

            except Exception as e:
                logger.error(f"Failed to load meta for DEX '{dex_name}': {e}")

        if target_dexes:
            missing = set(target_dexes) - set(self._dexes.keys())
            if missing:
                logger.warning(f"DEXes not found on-chain: {missing}")

    # ------------------------------------------------------------------ #
    # Lookup helpers
    # ------------------------------------------------------------------ #

    def is_hip3(self, coin: str) -> bool:
        """Returns True if coin uses "dex:coin" HIP-3 format."""
        return _is_hip3(coin)

    def parse_coin(self, coin: str) -> Tuple[Optional[str], str]:
        """Split "dex:coin" → (dex, coin_name).  "BTC" → (None, "BTC")."""
        return _parse_coin(coin)

    def get_asset_id(self, dex: str, coin: str) -> Optional[int]:
        """Integer asset ID for a HIP-3 asset (used in order placement)."""
        return self._dexes.get(dex, {}).get("assets", {}).get(coin, {}).get("asset_id")

    def get_sz_decimals(self, dex: str, coin: str) -> int:
        """Size decimal precision for a HIP-3 asset."""
        return self._dexes.get(dex, {}).get("assets", {}).get(coin, {}).get("sz_decimals", 3)

    def get_meta(self, dex: str) -> Dict:
        """Cached meta dict for a registered DEX."""
        return self._dexes.get(dex, {}).get("meta", {})

    def get_dex_names(self) -> List[str]:
        """List of registered DEX names."""
        return list(self._dexes.keys())

    def list_coins(self, dex: str) -> List[str]:
        """Available coin names for a DEX."""
        return list(self._dexes.get(dex, {}).get("assets", {}).keys())

    def build_coin_to_asset_map(self) -> Dict[str, int]:
        """
        Build {"dex:coin" → asset_integer_id} map for all registered DEXes.
        Inject this into Exchange.coin_to_asset so the SDK places HIP-3 orders.
        """
        result: Dict[str, int] = {}
        for dex_name, dex_info in self._dexes.items():
            for coin, asset_info in dex_info["assets"].items():
                result[f"{dex_name}:{coin}"] = asset_info["asset_id"]
        return result

    def summary(self) -> str:
        lines = []
        for dex_name, info in self._dexes.items():
            coins = list(info["assets"].keys())
            lines.append(f"  {dex_name} (idx={info['perp_dex_index']}): {coins}")
        return "\n".join(lines) if lines else "  (none)"
