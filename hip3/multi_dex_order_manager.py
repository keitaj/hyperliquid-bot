"""
Multi-DEX Order Manager
Extends OrderManager with HIP-3 builder-deployed perpetuals support.

Key design:
- Injects HIP-3 asset IDs ("dex:coin" → integer) into Exchange.coin_to_asset
  so the SDK's order() method works transparently for HIP-3 coins.
- Overrides position / order queries to aggregate across all configured DEXes.
- Coins are represented as "dex:coin" strings for HIP-3 (e.g. "xyz:XYZ100").
"""
import logging
from typing import Dict, List, Optional

from hip3.dex_registry import DEXRegistry
from hip3.multi_dex_market_data import MultiDexMarketData
from order_manager import OrderManager, OrderSide
from rate_limiter import api_wrapper

logger = logging.getLogger(__name__)


class MultiDexOrderManager(OrderManager):
    """
    Extends OrderManager with HIP-3 multi-DEX support.

    Args:
        exchange:      Hyperliquid Exchange instance.
        info:          Hyperliquid Info instance.
        account_address: Wallet address.
        registry:      Populated DEXRegistry.
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
    ):
        super().__init__(exchange, info, account_address)
        self.registry = registry
        self.market_data_ext = market_data  # typed reference to extended class
        self.hip3_dexes: List[str] = hip3_dexes or []

        # Inject HIP-3 asset IDs into the SDK's coin→assetID lookup table.
        # The SDK uses Exchange.coin_to_asset[coin_name] to build order actions.
        hip3_map = registry.build_coin_to_asset_map()
        if hasattr(exchange, "coin_to_asset") and hip3_map:
            exchange.coin_to_asset.update(hip3_map)
            logger.info(
                f"Injected {len(hip3_map)} HIP-3 asset IDs into Exchange.coin_to_asset: "
                + ", ".join(f"{k}={v}" for k, v in list(hip3_map.items())[:5])
                + (" ..." if len(hip3_map) > 5 else "")
            )

    # ------------------------------------------------------------------ #
    # Position queries — aggregate standard HL + all configured HIP-3 DEXes
    # ------------------------------------------------------------------ #

    def get_position(self, coin: str) -> Optional[Dict]:
        """Returns position for a coin.  Handles "dex:coin" format."""
        if self.registry.is_hip3(coin):
            dex, coin_name = self.registry.parse_coin(coin)
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
        HIP-3 position coins are prefixed: "coin" → "dex:coin".
        """
        # Standard HL positions
        all_positions = super().get_all_positions()

        # HIP-3 DEX positions
        for dex in self.hip3_dexes:
            try:
                user_state = self.market_data_ext.get_user_state(self.account_address, dex=dex)
                for p in user_state.get("assetPositions", []):
                    pos = dict(p["position"])
                    # Prefix coin so strategies can distinguish DEX-specific positions
                    if ":" not in pos.get("coin", ""):
                        pos["coin"] = f"{dex}:{pos['coin']}"
                    all_positions.append(pos)
            except Exception as e:
                logger.error(f"Error fetching positions for DEX '{dex}': {e}")

        return all_positions

    # ------------------------------------------------------------------ #
    # Open order queries — aggregate across DEXes
    # ------------------------------------------------------------------ #

    def get_open_orders(self, coin: Optional[str] = None) -> List[Dict]:
        """
        Returns open orders across standard HL + all configured HIP-3 DEXes.
        Optionally filtered by coin (supports "dex:coin" format).
        """
        # Determine if we're filtering by a specific DEX
        filter_dex: Optional[str] = None
        filter_coin_name: Optional[str] = None
        if coin:
            if self.registry.is_hip3(coin):
                filter_dex, filter_coin_name = self.registry.parse_coin(coin)
            else:
                filter_coin_name = coin

        # Standard HL orders
        all_orders: List[Dict] = []
        if filter_dex is None:
            try:
                hl_orders = api_wrapper.call(self.info.open_orders, self.account_address)
                if filter_coin_name:
                    hl_orders = [o for o in hl_orders if o["coin"] == filter_coin_name]
                all_orders.extend(hl_orders)
            except Exception as e:
                logger.error(f"Error fetching HL open orders: {e}")

        # HIP-3 DEX orders
        target_dexes = [filter_dex] if filter_dex else self.hip3_dexes
        for dex in target_dexes:
            try:
                dex_orders = self.market_data_ext.get_open_orders_dex(self.account_address, dex=dex)
                for order in dex_orders:
                    o = dict(order)
                    # Prefix coin name
                    if ":" not in o.get("coin", ""):
                        o["coin"] = f"{dex}:{o['coin']}"
                    if filter_coin_name and o["coin"] != coin:
                        continue
                    all_orders.append(o)
            except Exception as e:
                logger.error(f"Error fetching open orders for DEX '{dex}': {e}")

        return all_orders

    # ------------------------------------------------------------------ #
    # Cancel orders — across DEXes
    # ------------------------------------------------------------------ #

    def cancel_order(self, order_id: int, coin: str) -> bool:
        """Cancel a single order.  Handles "dex:coin" format."""
        # For HIP-3 coins, the exchange still cancels via the same endpoint
        # but we pass the full "dex:coin" string which the SDK resolves via coin_to_asset.
        return super().cancel_order(order_id, coin)

    def cancel_all_orders(self, coin: Optional[str] = None) -> int:
        """Cancel all open orders across standard HL + all configured HIP-3 DEXes."""
        cancelled = 0

        # Standard HL: only cancel if we're not filtering for a specific HIP-3 coin
        if coin is None or not self.registry.is_hip3(coin):
            cancelled += super().cancel_all_orders(coin)

        # HIP-3 DEXes
        filter_dex: Optional[str] = None
        filter_coin_name: Optional[str] = None
        if coin and self.registry.is_hip3(coin):
            filter_dex, filter_coin_name = self.registry.parse_coin(coin)

        target_dexes = [filter_dex] if filter_dex else self.hip3_dexes
        for dex in target_dexes:
            try:
                dex_orders = self.market_data_ext.get_open_orders_dex(self.account_address, dex=dex)
                for order in dex_orders:
                    order_coin_name = order["coin"]
                    if filter_coin_name and order_coin_name != filter_coin_name:
                        continue
                    full_coin = f"{dex}:{order_coin_name}"
                    if self.cancel_order(int(order["oid"]), full_coin):
                        cancelled += 1
            except Exception as e:
                logger.error(f"Error cancelling orders for DEX '{dex}': {e}")

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

            for order_id, order in list(self.active_orders.items()):
                if order_id not in open_order_ids:
                    # Determine which DEX to query fills from
                    dex: Optional[str] = None
                    if self.registry.is_hip3(order.coin):
                        dex, _ = self.registry.parse_coin(order.coin)

                    try:
                        if dex:
                            fills = self.market_data_ext._post({
                                "type": "userFills",
                                "user": self.account_address,
                                "dex": dex,
                            })
                        else:
                            fills = api_wrapper.call(self.info.user_fills, self.account_address)

                        filled = False
                        for fill in fills:
                            if int(fill["oid"]) == order_id:
                                order.filled_size = float(fill["sz"])
                                from order_manager import OrderStatus
                                order.status = OrderStatus.FILLED
                                filled = True
                                break
                        if not filled:
                            from order_manager import OrderStatus
                            order.status = OrderStatus.CANCELLED

                    except Exception as e:
                        logger.error(f"Error checking fill for order {order_id}: {e}")

                    del self.active_orders[order_id]

        except Exception as e:
            logger.error(f"Error updating order status: {e}")
