"""
Multi-DEX Order Manager
Extends OrderManager with HIP-3 builder-deployed perpetuals support.

As of hyperliquid-python-sdk 0.22.0:
- Exchange(perp_dexs=["xyz"]) auto-configures HIP-3 asset IDs in info.coin_to_asset
- Exchange.order("xyz:GOLD", ...) resolves via info.name_to_asset() transparently
- info.user_state(address, dex="xyz") and info.open_orders(address, dex="xyz") native

Coins are represented as "dex:coin" strings for HIP-3 (e.g. "xyz:GOLD", "flx:NVDA").
"""
import logging
from typing import Callable, Dict, List, Optional

from hip3.dex_registry import DEXRegistry
from rate_limiter import API_ERRORS
from hip3.multi_dex_market_data import MultiDexMarketData
from order_manager import OrderManager, OrderStatus
from rate_limiter import api_wrapper
from coin_utils import is_hip3, make_hip3_coin, parse_coin

logger = logging.getLogger(__name__)


class MultiDexOrderManager(OrderManager):
    """
    Extends OrderManager with HIP-3 multi-DEX support.

    Args:
        exchange:      Hyperliquid Exchange instance (initialised with perp_dexs).
        info:          Hyperliquid Info instance (same perp_dexs configuration).
        account_address: Wallet address.
        registry:      Populated DEXRegistry (for coin listing and parsing).
        market_data:   MultiDexMarketData instance.
        hip3_dexes:    Names of HIP-3 DEXes this manager should cover.
    """

    def __init__(
        self,
        exchange,
        info,
        account_address: str,
        registry: DEXRegistry,
        market_data: MultiDexMarketData,
        hip3_dexes: Optional[List[str]] = None,
        default_slippage: float = 0.01,
        mids_cache_ttl: float = 5.0,
    ):
        super().__init__(exchange, info, account_address,
                         default_slippage=default_slippage, mids_cache_ttl=mids_cache_ttl)
        self.registry = registry
        self.market_data_ext = market_data
        self.hip3_dexes: List[str] = hip3_dexes or []

        n = len(self.info.coin_to_asset)
        logger.info(f"MultiDexOrderManager ready — {n} assets in coin_to_asset (incl. HIP-3)")

    # ------------------------------------------------------------------ #
    # Shared HIP-3 helpers
    # ------------------------------------------------------------------ #

    def _resolve_target_dexes(
        self, coin: Optional[str],
    ) -> tuple:
        """Parse an optional coin filter into (filter_dex, filter_coin_name, target_dexes).

        Returns
        -------
        tuple[Optional[str], Optional[str], list[str]]
            (filter_dex, filter_coin_name, target_dexes)
        """
        filter_dex: Optional[str] = None
        filter_coin_name: Optional[str] = None
        if coin:
            if is_hip3(coin):
                filter_dex, filter_coin_name = parse_coin(coin)
            else:
                filter_coin_name = coin
        target_dexes = [filter_dex] if filter_dex else self.hip3_dexes
        return filter_dex, filter_coin_name, target_dexes

    def _collect_hip3_items(
        self,
        dexes: List[str],
        fetch_fn: Callable[[str], list],
        error_context: str,
    ) -> List[Dict]:
        """Iterate DEXes, fetch items, prefix coins, and handle errors.

        Parameters
        ----------
        dexes : list[str]
            DEX names to iterate.
        fetch_fn : callable(dex) -> list[dict]
            Returns raw items for a single DEX.  Each item must have a
            ``"coin"`` key with the bare coin name.
        error_context : str
            Label for error log messages (e.g. ``"positions"``, ``"open orders"``).

        Returns
        -------
        list[dict]
            Collected items with ``"coin"`` prefixed as ``"dex:coin"``.
        """
        results: List[Dict] = []
        for dex in dexes:
            try:
                for item in fetch_fn(dex):
                    prefixed = dict(item)
                    prefixed["coin"] = make_hip3_coin(dex, prefixed.get("coin", ""))
                    results.append(prefixed)
            except API_ERRORS as e:
                logger.error(f"Error fetching {error_context} for DEX '{dex}': {e}")
        return results

    # ------------------------------------------------------------------ #
    # Position queries — aggregate standard HL + all configured HIP-3 DEXes
    # ------------------------------------------------------------------ #

    def get_position(self, coin: str) -> Optional[Dict]:
        """Returns position for a coin. Handles "dex:coin" format."""
        if is_hip3(coin):
            dex, coin_name = parse_coin(coin)
            user_state = self.market_data_ext.get_user_state(self.account_address, dex=dex)
            for p in user_state.get("assetPositions", []):
                pos_coin = p["position"]["coin"]
                if pos_coin == coin_name or pos_coin == coin:
                    return p["position"]
            return None
        return super().get_position(coin)

    def get_all_positions(self) -> List[Dict]:
        """
        Returns all positions across standard HL + all configured HIP-3 DEXes.
        HIP-3 position coins are prefixed: "GOLD" → "xyz:GOLD".
        """
        all_positions = super().get_all_positions()

        def fetch_positions(dex: str) -> list:
            state = self.market_data_ext.get_user_state(self.account_address, dex=dex)
            return [p["position"] for p in state.get("assetPositions", [])]

        all_positions.extend(
            self._collect_hip3_items(self.hip3_dexes, fetch_positions, "positions")
        )
        return all_positions

    # ------------------------------------------------------------------ #
    # Open order queries — aggregate across DEXes
    # ------------------------------------------------------------------ #

    def get_open_orders(self, coin: Optional[str] = None) -> List[Dict]:
        """
        Returns open orders across standard HL + all configured HIP-3 DEXes.
        Optionally filtered by coin (supports "dex:coin" format).
        """
        filter_dex, filter_coin_name, target_dexes = self._resolve_target_dexes(coin)

        all_orders: List[Dict] = []

        # Standard HL orders
        if filter_dex is None:
            try:
                hl_orders = api_wrapper.call(self.info.open_orders, self.account_address)
                if filter_coin_name:
                    hl_orders = [o for o in hl_orders if o["coin"] == filter_coin_name]
                all_orders.extend(hl_orders)
            except API_ERRORS as e:
                logger.error(f"Error fetching HL open orders: {e}")

        # HIP-3 DEX orders
        def fetch_orders(dex: str) -> list:
            return self.market_data_ext.get_open_orders_dex(self.account_address, dex=dex)

        hip3_orders = self._collect_hip3_items(target_dexes, fetch_orders, "open orders")
        if filter_coin_name:
            hip3_orders = [o for o in hip3_orders if o["coin"] == coin]
        all_orders.extend(hip3_orders)

        return all_orders

    # ------------------------------------------------------------------ #
    # Cancel orders — across DEXes
    # ------------------------------------------------------------------ #

    def cancel_all_orders(self, coin: Optional[str] = None) -> int:
        """Cancel all open orders across standard HL + all configured HIP-3 DEXes."""
        cancelled = 0

        if coin is None or not is_hip3(coin):
            cancelled += super().cancel_all_orders(coin)

        filter_dex, filter_coin_name, target_dexes = self._resolve_target_dexes(coin)

        def fetch_orders(dex: str) -> list:
            return self.market_data_ext.get_open_orders_dex(self.account_address, dex=dex)

        hip3_orders = self._collect_hip3_items(target_dexes, fetch_orders, "orders to cancel")
        if filter_coin_name:
            hip3_orders = [o for o in hip3_orders if o["coin"] == coin]

        to_cancel = [{"coin": o["coin"], "oid": int(o["oid"])} for o in hip3_orders]
        if to_cancel:
            cancelled += self.bulk_cancel_orders(to_cancel)

        logger.info(f"Cancelled {cancelled} orders across all DEXes")
        return cancelled

    # ------------------------------------------------------------------ #
    # Order status update — aggregate across DEXes
    # ------------------------------------------------------------------ #

    def update_order_status(self):
        """Update status for tracked orders, checking fills across all DEXes."""
        try:
            open_orders = self.get_open_orders()
            open_order_ids = {int(o["oid"]) for o in open_orders}

            # Find orders that are no longer on the book
            disappeared = [
                (oid, order) for oid, order in self.active_orders.items()
                if oid not in open_order_ids
            ]
            if not disappeared:
                return

            # Group disappeared orders by DEX so we fetch fills once per DEX
            by_dex: Dict[Optional[str], list] = {}  # None = standard HL
            for order_id, order in disappeared:
                dex: Optional[str] = None
                if is_hip3(order.coin):
                    dex, _ = parse_coin(order.coin)
                by_dex.setdefault(dex, []).append((order_id, order))

            # Fetch fills once per DEX and build oid->total_size lookup
            for dex, orders in by_dex.items():
                try:
                    if dex:
                        fills = self.market_data_ext.get_user_fills_dex(self.account_address, dex)
                    else:
                        fills = api_wrapper.call(self.info.user_fills, self.account_address)

                    filled_by_oid: Dict[int, float] = {}
                    for fill in fills:
                        foid = int(fill["oid"])
                        if foid in filled_by_oid:
                            filled_by_oid[foid] += float(fill["sz"])
                        else:
                            filled_by_oid[foid] = float(fill["sz"])

                    for order_id, order in orders:
                        if order_id in filled_by_oid:
                            order.filled_size = filled_by_oid[order_id]
                            order.status = OrderStatus.FILLED
                        else:
                            order.status = OrderStatus.CANCELLED
                        del self.active_orders[order_id]

                except API_ERRORS as e:
                    logger.error(f"Error checking fills for DEX '{dex}': {e}")
                    # Still clean up orders to avoid retrying indefinitely
                    for order_id, order in orders:
                        order.status = OrderStatus.CANCELLED
                        del self.active_orders[order_id]

        except API_ERRORS as e:
            logger.error(f"Error updating order status: {e}")
