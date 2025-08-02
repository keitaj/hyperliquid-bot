import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

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
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class OrderManager:
    def __init__(self, exchange: Exchange, info: Info, account_address: str):
        self.exchange = exchange
        self.info = info
        self.account_address = account_address
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
        reduce_only: bool = False
    ) -> Optional[Order]:
        order = Order(
            id=None,
            coin=coin,
            side=side,
            size=size,
            price=0,
            order_type={"market": {}},
            reduce_only=reduce_only
        )
        
        return self._place_order(order)
    
    def _place_order(self, order: Order) -> Optional[Order]:
        try:
            result = self.exchange.order(
                coin=order.coin,
                is_buy=(order.side == OrderSide.BUY),
                sz=order.size,
                limit_px=order.price,
                order_type=order.order_type,
                reduce_only=order.reduce_only
            )
            
            if result and 'status' in result and result['status'] == 'ok':
                if 'response' in result and 'data' in result['response']:
                    order_data = result['response']['data']
                    if 'statuses' in order_data and order_data['statuses']:
                        status_info = order_data['statuses'][0]
                        if 'oid' in status_info:
                            order.id = int(status_info['oid'])
                            self.active_orders[order.id] = order
                            logger.info(f"Order placed successfully: {order.id}")
                            return order
                            
            logger.error(f"Failed to place order: {result}")
            order.status = OrderStatus.REJECTED
            return None
            
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            order.status = OrderStatus.REJECTED
            return None
    
    def cancel_order(self, order_id: int, coin: str) -> bool:
        try:
            result = self.exchange.cancel(coin=coin, oid=order_id)
            
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
            open_orders = self.info.open_orders(self.account_address)
            
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
            open_orders = self.info.open_orders(self.account_address)
            
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
                    fills = self.info.user_fills(self.account_address)
                    
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
            user_state = self.info.user_state(self.account_address)
            
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
            user_state = self.info.user_state(self.account_address)
            
            if 'assetPositions' in user_state:
                return [p['position'] for p in user_state['assetPositions']]
            return []
            
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []