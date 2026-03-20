import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from rate_limiter import api_wrapper

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Order:
    id: Optional[int]
    coin: str
    side: OrderSide
    size: float
    price: float
    order_type: Dict
    reduce_only: bool = False
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    timestamp: Optional[datetime] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class OrderManager:
    def __init__(self, exchange: Exchange, info: Info, account_address: str, default_slippage: float = 0.01):
        self.exchange = exchange
        self.info = info
        self.account_address = account_address
        self.default_slippage = default_slippage
        self.active_orders: Dict[int, Order] = {}
        
    def create_limit_order(
        self,
        coin: str,
        side: OrderSide,
        size: float,
        price: float,
        reduce_only: bool = False,
        post_only: bool = True
    ) -> Optional[Order]:
        order = Order(
            id=None,
            coin=coin,
            side=side,
            size=size,
            price=price,
            order_type={"limit": {"tif": "Gtc"}},
            reduce_only=reduce_only
        )
        
        if post_only:
            order.order_type["limit"]["tif"] = "Alo"
            
        return self._place_order(order)
    
    def create_market_order(
        self,
        coin: str,
        side: OrderSide,
        size: float,
        reduce_only: bool = False,
        slippage: float = None
    ) -> Optional[Order]:
        """Place a market order using an aggressive IOC limit order.

        The SDK's ``exchange.order`` does not accept ``{"market": {}}``.
        Instead we use an IOC (Immediate-or-Cancel) limit order with a
        slippage-adjusted price to simulate market execution.
        """
        if slippage is None:
            slippage = self.default_slippage
        try:
            mid_price = self._get_mid_price(coin)
            if mid_price <= 0:
                logger.error(f"Cannot determine mid price for {coin}")
                return None

            if side == OrderSide.BUY:
                limit_price = mid_price * (1 + slippage)
            else:
                limit_price = mid_price * (1 - slippage)

            # Round to 5 sig figs + 6 decimals (Hyperliquid perp format)
            limit_price = round(float(f"{limit_price:.5g}"), 6)
        except Exception as e:
            logger.error(f"Error calculating market price for {coin}: {e}")
            return None

        order = Order(
            id=None,
            coin=coin,
            side=side,
            size=size,
            price=limit_price,
            order_type={"limit": {"tif": "Ioc"}},
            reduce_only=reduce_only
        )

        return self._place_order(order)
    
    def _place_order(self, order: Order) -> Optional[Order]:
        try:
            result = api_wrapper.call(
                self.exchange.order,
                order.coin,
                (order.side == OrderSide.BUY),
                order.size,
                order.price,
                order.order_type,
                order.reduce_only
            )
            
            if result and 'status' in result and result['status'] == 'ok':
                if 'response' in result and 'data' in result['response']:
                    order_data = result['response']['data']
                    if 'statuses' in order_data and order_data['statuses']:
                        status_info = order_data['statuses'][0]

                        # Extract oid from various response formats:
                        # - {'oid': 123}           (immediately filled)
                        # - {'resting': {'oid': 123}}  (limit order on book)
                        # - {'filled': {'oid': 123}}   (IOC filled)
                        oid = None
                        if 'oid' in status_info:
                            oid = int(status_info['oid'])
                        elif 'resting' in status_info:
                            oid = int(status_info['resting']['oid'])
                        elif 'filled' in status_info:
                            oid = int(status_info['filled']['oid'])

                        if oid is not None:
                            order.id = oid
                            self.active_orders[order.id] = order
                            logger.info(f"Order placed successfully: {order.id}")
                            return order

                        # Check for error in status
                        if 'error' in status_info:
                            logger.error(f"Order rejected: {status_info['error']}")
                            order.status = OrderStatus.REJECTED
                            return None

            logger.error(f"Failed to place order: {result}")
            order.status = OrderStatus.REJECTED
            return None
            
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            order.status = OrderStatus.REJECTED
            return None
    
    # Short-lived cache for all_mids to avoid redundant API calls within a cycle.
    # Key: dex name ('' for standard), Value: (timestamp, mids_dict)
    _mids_cache: Dict[str, tuple] = {}
    _MIDS_CACHE_TTL = 5.0  # seconds

    def _get_cached_mids(self, dex: str = '') -> Dict:
        """Return all_mids for a DEX, using a short-lived cache."""
        now = time.time()
        cached = self._mids_cache.get(dex)
        if cached and (now - cached[0]) < self._MIDS_CACHE_TTL:
            return cached[1]

        mids = api_wrapper.call(self.info.all_mids, dex=dex) if dex else api_wrapper.call(self.info.all_mids)
        self._mids_cache[dex] = (now, mids)
        return mids

    def _get_mid_price(self, coin: str) -> float:
        """Get mid price for a coin. Works with both standard and HIP-3 coins.

        Uses a short-lived cache to avoid redundant API calls when
        multiple coins from the same DEX are queried in the same cycle.
        """
        try:
            all_mids = self._get_cached_mids()

            # Direct lookup (standard coins or if already in mids)
            if coin in all_mids:
                return float(all_mids[coin])

            # HIP-3 "dex:coin" -- try DEX-scoped all_mids
            if ":" in coin:
                dex = coin.split(":")[0]
                try:
                    dex_mids = self._get_cached_mids(dex=dex)
                    if coin in dex_mids:
                        return float(dex_mids[coin])
                    base_coin = coin.split(":")[-1]
                    if base_coin in dex_mids:
                        return float(dex_mids[base_coin])
                except Exception:
                    pass

            return 0.0
        except Exception as e:
            logger.error(f"Error fetching mid price for {coin}: {e}")
            return 0.0

    def cancel_order(self, order_id: int, coin: str) -> bool:
        try:
            result = api_wrapper.call(self.exchange.cancel, coin, order_id)
            
            if result and 'status' in result and result['status'] == 'ok':
                if order_id in self.active_orders:
                    self.active_orders[order_id].status = OrderStatus.CANCELLED
                    del self.active_orders[order_id]
                logger.info(f"Order {order_id} cancelled successfully")
                return True
                
            logger.error(f"Failed to cancel order {order_id}: {result}")
            return False
            
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return False
    
    def cancel_all_orders(self, coin: Optional[str] = None) -> int:
        cancelled_count = 0
        
        try:
            open_orders = api_wrapper.call(self.info.open_orders, self.account_address)
            
            for order in open_orders:
                if coin is None or order['coin'] == coin:
                    if self.cancel_order(int(order['oid']), order['coin']):
                        cancelled_count += 1
                        
            logger.info(f"Cancelled {cancelled_count} orders")
            return cancelled_count
            
        except Exception as e:
            logger.error(f"Error cancelling all orders: {e}")
            return cancelled_count
    
    def get_open_orders(self, coin: Optional[str] = None) -> List[Dict]:
        try:
            open_orders = api_wrapper.call(self.info.open_orders, self.account_address)
            
            if coin:
                return [o for o in open_orders if o['coin'] == coin]
            return open_orders
            
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            return []
    
    def update_order_status(self):
        try:
            open_orders = self.get_open_orders()
            open_order_ids = {int(o['oid']) for o in open_orders}
            
            for order_id, order in list(self.active_orders.items()):
                if order_id not in open_order_ids:
                    fills = api_wrapper.call(self.info.user_fills, self.account_address)
                    
                    for fill in fills:
                        if int(fill['oid']) == order_id:
                            order.filled_size = float(fill['sz'])
                            order.status = OrderStatus.FILLED
                            break
                    else:
                        order.status = OrderStatus.CANCELLED
                        
                    del self.active_orders[order_id]
                    
        except Exception as e:
            logger.error(f"Error updating order status: {e}")
    
    def get_position(self, coin: str) -> Optional[Dict]:
        try:
            user_state = api_wrapper.call(self.info.user_state, self.account_address)
            
            if 'assetPositions' in user_state:
                for position in user_state['assetPositions']:
                    if position['position']['coin'] == coin:
                        return position['position']
            return None
            
        except Exception as e:
            logger.error(f"Error fetching position for {coin}: {e}")
            return None
    
    def get_all_positions(self) -> List[Dict]:
        try:
            user_state = api_wrapper.call(self.info.user_state, self.account_address)
            
            if 'assetPositions' in user_state:
                return [p['position'] for p in user_state['assetPositions']]
            return []
            
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []