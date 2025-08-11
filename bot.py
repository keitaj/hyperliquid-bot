import logging
import time
import signal
from typing import Dict, List, Optional
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from config import Config
from market_data import MarketDataManager
from order_manager import OrderManager
from risk_manager import RiskManager
from validation import MarginValidator
from strategies import (
    SimpleMAStrategy,
    RSIStrategy,
    BollingerBandsStrategy,
    MACDStrategy,
    GridTradingStrategy,
    BreakoutStrategy
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class HyperliquidBot:
    def __init__(self, strategy_name: str = "simple_ma", coins: List[str] = None, strategy_config: Dict = None):
        Config.validate()
        self.account_address = Config.ACCOUNT_ADDRESS
        self.info = Info(Config.API_URL, skip_ws=True)
        self.exchange = Exchange(
            wallet=self._load_wallet(),
            base_url=Config.API_URL
        )
        self.running = False
        self.connection_retry_count = 0
        self.last_connection_reset = time.time()

        self.market_data = MarketDataManager(self.info)
        self.order_manager = OrderManager(self.exchange, self.info, self.account_address)
        self.risk_manager = RiskManager(self.info, self.account_address, {
            'max_leverage': 3.0,
            'max_position_size_pct': 0.2,
            'max_drawdown_pct': 0.1,
            'daily_loss_limit_pct': 0.05
        })

        # Default strategy configurations
        default_configs = {
            'simple_ma': {
                'fast_ma_period': 10,
                'slow_ma_period': 30,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 5,
                'stop_loss_percent': 2
            },
            'rsi': {
                'rsi_period': 14,
                'oversold_threshold': 30,
                'overbought_threshold': 70,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 5,
                'stop_loss_percent': 2
            },
            'bollinger_bands': {
                'bb_period': 20,
                'std_dev': 2,
                'squeeze_threshold': 0.02,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 5,
                'stop_loss_percent': 2
            },
            'macd': {
                'fast_ema': 12,
                'slow_ema': 26,
                'signal_ema': 9,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 5,
                'stop_loss_percent': 2
            },
            'grid_trading': {
                'grid_levels': 10,
                'grid_spacing_pct': 0.5,
                'position_size_per_grid': 50,
                'max_positions': 5,
                'range_period': 100,
                'take_profit_percent': 2,
                'stop_loss_percent': 5
            },
            'breakout': {
                'lookback_period': 20,
                'volume_multiplier': 1.5,
                'breakout_confirmation_bars': 2,
                'atr_period': 14,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 7,
                'stop_loss_percent': 3
            }
        }

        # Use provided config or default config for the strategy
        config = strategy_config or default_configs.get(strategy_name, {})

        # Store strategy name and coins for validation
        self.strategy_name = strategy_name
        self.trading_coins = coins or ['BTC', 'ETH']
        self.strategy_config = config

        # Strategy factory
        strategy_map = {
            'simple_ma': SimpleMAStrategy,
            'rsi': RSIStrategy,
            'bollinger_bands': BollingerBandsStrategy,
            'macd': MACDStrategy,
            'grid_trading': GridTradingStrategy,
            'breakout': BreakoutStrategy
        }

        if strategy_name in strategy_map:
            self.strategy = strategy_map[strategy_name](
                self.market_data,
                self.order_manager,
                config
            )
            logger.info(f"Initialized {strategy_name} strategy")
        else:
            available_strategies = ', '.join(strategy_map.keys())
            raise ValueError(f"Unknown strategy: {strategy_name}. Available strategies: {available_strategies}")

        self.coins = coins or ["BTC", "ETH", "SOL"]

    def _load_wallet(self):
        from eth_account import Account
        return Account.from_key(Config.PRIVATE_KEY)

    def get_user_state(self) -> Dict:
        try:
            from rate_limiter import api_wrapper
            user_state = api_wrapper.call(self.info.user_state, self.account_address)
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

        # Validate margin requirements before starting
        if not self._validate_trading_configuration():
            logger.error("Bot startup cancelled due to configuration validation failure")
            logger.error("Please adjust your configuration based on the recommendations above")
            return

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        consecutive_errors = 0

        while self.running:
            try:
                # Check if we should reset connections due to too many errors
                current_time = time.time()
                if consecutive_errors > 10:
                    if current_time - self.last_connection_reset > 300:  # 5 minutes
                        self._reset_connections()
                        self.last_connection_reset = current_time
                        consecutive_errors = 0

                self._trading_loop()
                consecutive_errors = 0  # Reset on successful iteration
                time.sleep(10)  # Increased delay to avoid rate limits

            except ConnectionError as e:
                consecutive_errors += 1
                logger.error(f"Connection error (#{consecutive_errors}): {e}")
                time.sleep(min(consecutive_errors * 5, 60))  # Exponential backoff

            except KeyboardInterrupt:
                logger.info("Stopping bot...")
                self.running = False

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in main loop (#{consecutive_errors}): {e}")
                time.sleep(10)  # Longer delay on error

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

    def _signal_handler(self, signum, frame):
        logger.info("Received shutdown signal")
        self.running = False
        try:
            # Set a timeout for order cancellation to avoid hanging
            import threading
            def cancel_orders():
                self.order_manager.cancel_all_orders()
                logger.info("All orders cancelled")
            
            cancel_thread = threading.Thread(target=cancel_orders, daemon=True)
            cancel_thread.start()
            cancel_thread.join(timeout=5)  # 5 second timeout
            
            if cancel_thread.is_alive():
                logger.warning("Order cancellation timed out, forcing shutdown")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        
        # Force exit if still running after a short delay
        import sys
        import time
        time.sleep(1)
        if self.running:
            logger.info("Forcing immediate shutdown")
            sys.exit(0)

    def _validate_trading_configuration(self) -> bool:
        """Validate trading configuration and margin requirements"""
        try:
            # Initialize validator
            validator = MarginValidator(self.info, self.account_address)

            # First check minimum requirements
            min_check = validator.validate_minimum_requirements()
            if not min_check.is_valid:
                logger.error(min_check.message)
                if min_check.recommendations:
                    for rec in min_check.recommendations:
                        logger.error(f"  • {rec}")
                return False

            # Get current prices for position size calculation
            current_prices = {}
            for coin in self.trading_coins:
                market_data = self.market_data.get_market_data(coin)
                if market_data:
                    current_prices[coin] = market_data.mid_price
                else:
                    logger.error(f"Could not get market price for {coin}")
                    logger.error("Unable to validate configuration without current market prices")
                    logger.error("Please check your internet connection and try again")
                    return False

            # Validate strategy-specific configuration
            validation_result = validator.validate_strategy_config(
                strategy_name=self.strategy_name,
                strategy_config=self.strategy_config,
                coins=self.trading_coins,
                current_prices=current_prices
            )

            if not validation_result.is_valid:
                logger.error("")
                logger.error("💡 SUGGESTED CONFIGURATION:")

                # Get account info for suggestions
                account_value, _ = validator.get_account_info()

                # Generate conservative suggestion
                conservative_config = validator.suggest_optimal_config(
                    strategy_name=self.strategy_name,
                    account_value=account_value,
                    coins=self.trading_coins,
                    aggressive=False
                )

                logger.info("Conservative (Recommended):")
                for key, value in conservative_config.items():
                    logger.info(f"  {key}: {value}")

                # Generate aggressive suggestion
                aggressive_config = validator.suggest_optimal_config(
                    strategy_name=self.strategy_name,
                    account_value=account_value,
                    coins=self.trading_coins,
                    aggressive=True
                )

                logger.info("Aggressive (Higher Risk):")
                for key, value in aggressive_config.items():
                    logger.info(f"  {key}: {value}")

                return False

            logger.info("")
            logger.info("✅ Configuration validation passed. Bot is ready to trade.")
            logger.info("")
            return True

        except Exception as e:
            logger.error(f"Error during configuration validation: {e}")
            logger.error("Proceeding with caution...")
            return True  # Allow to proceed but with warning

    def _reset_connections(self):
        """Reset connections when encountering persistent errors"""
        try:
            logger.info("Resetting connections due to persistent errors...")
            time.sleep(5)  # Give some time before reconnecting

            # Re-initialize connections
            self.info = Info(Config.API_URL, skip_ws=True)
            self.exchange = Exchange(
                wallet=self._load_wallet(),
                base_url=Config.API_URL
            )

            # Re-initialize managers with new connections
            self.market_data = MarketDataManager(self.info)
            self.order_manager = OrderManager(self.exchange, self.info, self.account_address)
            self.risk_manager = RiskManager(self.info, self.account_address, {
                'max_leverage': 3.0,
                'max_position_size_pct': 0.2,
                'max_drawdown_pct': 0.1,
                'daily_loss_limit_pct': 0.05
            })

            # Re-initialize strategy with new connections
            strategy_config = self.strategy.config if hasattr(self.strategy, 'config') else {}
            self.strategy.__init__(self.market_data, self.order_manager, strategy_config)

            logger.info("Connections reset successfully")
            self.connection_retry_count = 0

        except Exception as e:
            logger.error(f"Failed to reset connections: {e}")
            self.connection_retry_count += 1
            if self.connection_retry_count > 5:
                logger.critical("Max connection retry attempts reached. Exiting...")
                self.running = False

    def stop(self):
        self.running = False
        logger.info("Bot stopped")


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description='Hyperliquid Trading Bot')
    parser.add_argument(
        '--strategy',
        type=str,
        default='simple_ma',
        choices=['simple_ma', 'rsi', 'bollinger_bands', 'macd', 'grid_trading', 'breakout'],
        help='Trading strategy to use'
    )
    parser.add_argument(
        '--coins',
        type=str,
        nargs='+',
        default=['BTC', 'ETH', 'SOL'],
        help='Coins to trade'
    )

    # Common strategy parameters
    parser.add_argument('--position-size-usd', type=float, help='Position size in USD')
    parser.add_argument('--max-positions', type=int, help='Maximum number of positions')
    parser.add_argument('--take-profit-percent', type=float, help='Take profit percentage')
    parser.add_argument('--stop-loss-percent', type=float, help='Stop loss percentage')

    # Simple MA strategy parameters
    parser.add_argument('--fast-ma-period', type=int, help='Fast MA period (simple_ma)')
    parser.add_argument('--slow-ma-period', type=int, help='Slow MA period (simple_ma)')

    # RSI strategy parameters
    parser.add_argument('--rsi-period', type=int, help='RSI period (rsi)')
    parser.add_argument('--oversold-threshold', type=int, help='RSI oversold threshold (rsi)')
    parser.add_argument('--overbought-threshold', type=int, help='RSI overbought threshold (rsi)')

    # Bollinger Bands parameters
    parser.add_argument('--bb-period', type=int, help='Bollinger Bands period (bollinger_bands)')
    parser.add_argument('--std-dev', type=float, help='Standard deviation (bollinger_bands)')
    parser.add_argument('--squeeze-threshold', type=float, help='Squeeze threshold (bollinger_bands)')

    # MACD parameters
    parser.add_argument('--fast-ema', type=int, help='Fast EMA period (macd)')
    parser.add_argument('--slow-ema', type=int, help='Slow EMA period (macd)')
    parser.add_argument('--signal-ema', type=int, help='Signal EMA period (macd)')

    # Grid Trading parameters
    parser.add_argument('--grid-levels', type=int, help='Number of grid levels (grid_trading)')
    parser.add_argument('--grid-spacing-pct', type=float, help='Grid spacing percentage (grid_trading)')
    parser.add_argument('--position-size-per-grid', type=float, help='Position size per grid (grid_trading)')
    parser.add_argument('--range-period', type=int, help='Range period (grid_trading)')

    # Breakout parameters
    parser.add_argument('--lookback-period', type=int, help='Lookback period (breakout)')
    parser.add_argument('--volume-multiplier', type=float, help='Volume multiplier (breakout)')
    parser.add_argument('--breakout-confirmation-bars', type=int, help='Breakout confirmation bars (breakout)')
    parser.add_argument('--atr-period', type=int, help='ATR period (breakout)')

    args = parser.parse_args()

    # Build strategy config from command line arguments
    strategy_config = {}

    # Common parameters
    if args.position_size_usd is not None:
        strategy_config['position_size_usd'] = args.position_size_usd
    if args.max_positions is not None:
        strategy_config['max_positions'] = args.max_positions
    if args.take_profit_percent is not None:
        strategy_config['take_profit_percent'] = args.take_profit_percent
    if args.stop_loss_percent is not None:
        strategy_config['stop_loss_percent'] = args.stop_loss_percent

    # Strategy-specific parameters
    if args.strategy == 'simple_ma':
        if args.fast_ma_period is not None:
            strategy_config['fast_ma_period'] = args.fast_ma_period
        if args.slow_ma_period is not None:
            strategy_config['slow_ma_period'] = args.slow_ma_period

    elif args.strategy == 'rsi':
        if args.rsi_period is not None:
            strategy_config['rsi_period'] = args.rsi_period
        if args.oversold_threshold is not None:
            strategy_config['oversold_threshold'] = args.oversold_threshold
        if args.overbought_threshold is not None:
            strategy_config['overbought_threshold'] = args.overbought_threshold

    elif args.strategy == 'bollinger_bands':
        if args.bb_period is not None:
            strategy_config['bb_period'] = args.bb_period
        if args.std_dev is not None:
            strategy_config['std_dev'] = args.std_dev
        if args.squeeze_threshold is not None:
            strategy_config['squeeze_threshold'] = args.squeeze_threshold

    elif args.strategy == 'macd':
        if args.fast_ema is not None:
            strategy_config['fast_ema'] = args.fast_ema
        if args.slow_ema is not None:
            strategy_config['slow_ema'] = args.slow_ema
        if args.signal_ema is not None:
            strategy_config['signal_ema'] = args.signal_ema

    elif args.strategy == 'grid_trading':
        if args.grid_levels is not None:
            strategy_config['grid_levels'] = args.grid_levels
        if args.grid_spacing_pct is not None:
            strategy_config['grid_spacing_pct'] = args.grid_spacing_pct
        if args.position_size_per_grid is not None:
            strategy_config['position_size_per_grid'] = args.position_size_per_grid
        if args.range_period is not None:
            strategy_config['range_period'] = args.range_period

    elif args.strategy == 'breakout':
        if args.lookback_period is not None:
            strategy_config['lookback_period'] = args.lookback_period
        if args.volume_multiplier is not None:
            strategy_config['volume_multiplier'] = args.volume_multiplier
        if args.breakout_confirmation_bars is not None:
            strategy_config['breakout_confirmation_bars'] = args.breakout_confirmation_bars
        if args.atr_period is not None:
            strategy_config['atr_period'] = args.atr_period

    logger.info(f"Starting bot with {args.strategy} strategy for coins: {args.coins}")
    if strategy_config:
        logger.info(f"Custom parameters: {json.dumps(strategy_config, indent=2)}")

    bot = HyperliquidBot(
        strategy_name=args.strategy,
        coins=args.coins,
        strategy_config=strategy_config if strategy_config else None
    )
    bot.run()