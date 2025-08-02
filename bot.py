import logging
import time
from typing import Dict, List, Optional
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from config import Config
from market_data import MarketDataManager
from order_manager import OrderManager
from risk_manager import RiskManager
from strategies import SimpleMAStrategy

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class HyperliquidBot:
    def __init__(self, strategy_name: str = "simple_ma", coins: List[str] = None):
        Config.validate()
        self.account_address = Config.ACCOUNT_ADDRESS
        self.info = Info(Config.API_URL, skip_ws=True)
        self.exchange = Exchange(
            wallet=self._load_wallet(),
            base_url=Config.API_URL
        )
        self.running = False
        
        self.market_data = MarketDataManager(self.info)
        self.order_manager = OrderManager(self.exchange, self.info, self.account_address)
        self.risk_manager = RiskManager(self.info, self.account_address, {
            'max_leverage': 3.0,
            'max_position_size_pct': 0.2,
            'max_drawdown_pct': 0.1,
            'daily_loss_limit_pct': 0.05
        })
        
        strategy_config = {
            'fast_ma_period': 10,
            'slow_ma_period': 30,
            'position_size_usd': 100,
            'max_positions': 3,
            'take_profit_percent': 5,
            'stop_loss_percent': 2
        }
        
        if strategy_name == "simple_ma":
            self.strategy = SimpleMAStrategy(
                self.market_data,
                self.order_manager,
                strategy_config
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy_name}")
            
        self.coins = coins or ["BTC", "ETH", "SOL"]
        
    def _load_wallet(self):
        from eth_account import Account
        return Account.from_key(Config.SECRET_KEY)
    
    def get_user_state(self) -> Dict:
        try:
            user_state = self.info.user_state(self.account_address)
            logger.info(f"User state retrieved successfully")
            return user_state
        except Exception as e:
            logger.error(f"Error getting user state: {e}")
            return {}
    
    def get_all_mids(self) -> Dict[str, float]:
        try:
            all_mids = self.info.all_mids()
            return all_mids
        except Exception as e:
            logger.error(f"Error getting market prices: {e}")
            return {}
    
    def get_open_orders(self) -> List[Dict]:
        try:
            open_orders = self.info.open_orders(self.account_address)
            return open_orders
        except Exception as e:
            logger.error(f"Error getting open orders: {e}")
            return []
    
    def place_order(
        self,
        coin: str,
        is_buy: bool,
        sz: float,
        limit_px: float,
        order_type: Dict,
        reduce_only: bool = False
    ):
        try:
            order_result = self.exchange.order(
                coin=coin,
                is_buy=is_buy,
                sz=sz,
                limit_px=limit_px,
                order_type=order_type,
                reduce_only=reduce_only
            )
            logger.info(f"Order placed: {order_result}")
            return order_result
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None
    
    def cancel_order(self, coin: str, oid: int):
        try:
            cancel_result = self.exchange.cancel(coin=coin, oid=oid)
            logger.info(f"Order cancelled: {cancel_result}")
            return cancel_result
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return None
    
    def run(self):
        logger.info("Starting Hyperliquid trading bot...")
        self.running = True
        
        network = "Testnet" if Config.USE_TESTNET else "Mainnet"
        logger.info(f"Connected to {network}")
        
        user_state = self.get_user_state()
        if user_state:
            logger.info(f"Account: {self.account_address}")
            
        while self.running:
            try:
                self._trading_loop()
                time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Stopping bot...")
                self.running = False
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(5)
    
    def _trading_loop(self):
        risk_checks = self.risk_manager.check_risk_limits()
        if not risk_checks['all_checks_passed']:
            logger.warning(f"Risk limits exceeded: {risk_checks.get('reason')}")
            self.order_manager.cancel_all_orders()
            return
            
        self.order_manager.update_order_status()
        
        self.strategy.run(self.coins)
        
        if int(time.time()) % 60 == 0:
            risk_summary = self.risk_manager.get_risk_summary()
            logger.info(f"Risk summary: {risk_summary}")
    
    def stop(self):
        self.running = False
        logger.info("Bot stopped")


if __name__ == "__main__":
    bot = HyperliquidBot()
    bot.run()