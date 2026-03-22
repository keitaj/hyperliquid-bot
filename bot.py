import logging
import time
import signal
import warnings
from typing import Dict, List
from hyperliquid.exchange import Exchange
from config import Config
from market_data import MarketDataManager
from order_manager import OrderManager
from risk_manager import RiskManager
from validation import MarginValidator
from hip3 import DEXRegistry, MultiDexMarketData, MultiDexOrderManager
from order_manager import OrderSide
from strategies import (
    SimpleMAStrategy,
    RSIStrategy,
    BollingerBandsStrategy,
    MACDStrategy,
    GridTradingStrategy,
    BreakoutStrategy,
    MarketMakingStrategy
)

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class HyperliquidBot:
    def __init__(self, strategy_name: str = "simple_ma", coins: List[str] = None, strategy_config: Dict = None,
                 main_loop_interval: float = 10, market_order_slippage: float = 0.01):
        Config.validate()
        self.account_address = Config.ACCOUNT_ADDRESS
        self.running = False
        self.connection_retry_count = 0
        self.last_connection_reset = time.time()
        self.main_loop_interval = main_loop_interval
        self.market_order_slippage = market_order_slippage

        # ------------------------------------------------------------------ #
        # HIP-3 Multi-DEX setup
        # ------------------------------------------------------------------ #
        self.hip3_dexes: List[str] = Config.TRADING_DEXES
        self.registry = DEXRegistry(Config.API_URL)

        if self.hip3_dexes:
            # Discover coins for each DEX (used to build trading_coins list)
            self.registry.discover(self.hip3_dexes)

            self.exchange = Exchange(
                wallet=self._load_wallet(),
                base_url=Config.API_URL,
                perp_dexs=self._build_perp_dexs(),
            )
            # Reuse the Info object created inside Exchange to avoid duplicate API calls.
            self.info = self.exchange.info

            logger.info("Registered DEXes:\n" + self.registry.summary())
            self.market_data = MultiDexMarketData(self.info, self.registry, Config.API_URL)
            self.order_manager = MultiDexOrderManager(
                exchange=self.exchange,
                info=self.info,
                account_address=self.account_address,
                registry=self.registry,
                market_data=self.market_data,
                hip3_dexes=self.hip3_dexes,
                default_slippage=self.market_order_slippage,
            )
        else:
            self.exchange = Exchange(
                wallet=self._load_wallet(),
                base_url=Config.API_URL,
            )
            self.info = self.exchange.info
            self.market_data = MarketDataManager(self.info)
            self.order_manager = OrderManager(self.exchange, self.info, self.account_address,
                                              default_slippage=self.market_order_slippage)

        self.risk_config = self._build_risk_config()
        self.risk_manager = RiskManager(self.info, self.account_address, self.risk_config)

        # Default strategy configurations
        default_configs = {
            'simple_ma': {
                'fast_ma_period': 10,
                'slow_ma_period': 30,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 5,
                'stop_loss_percent': 2,
                'candle_interval': '5m',
            },
            'rsi': {
                'rsi_period': 14,
                'oversold_threshold': 30,
                'overbought_threshold': 70,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 5,
                'stop_loss_percent': 2,
                'candle_interval': '15m',
                'rsi_extreme_low': 25,
                'rsi_moderate_low': 35,
                'size_multiplier_extreme': 1.5,
                'size_multiplier_moderate': 1.2,
            },
            'bollinger_bands': {
                'bb_period': 20,
                'std_dev': 2,
                'squeeze_threshold': 0.02,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 5,
                'stop_loss_percent': 2,
                'candle_interval': '15m',
                'volatility_expansion_threshold': 1.5,
                'high_band_width_threshold': 0.05,
                'high_band_width_multiplier': 0.8,
                'low_band_width_threshold': 0.02,
                'low_band_width_multiplier': 1.2,
            },
            'macd': {
                'fast_ema': 12,
                'slow_ema': 26,
                'signal_ema': 9,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 5,
                'stop_loss_percent': 2,
                'candle_interval': '15m',
                'divergence_lookback': 20,
                'histogram_strength_high': 0.5,
                'histogram_strength_low': 0.1,
                'histogram_multiplier_high': 1.3,
                'histogram_multiplier_low': 0.7,
            },
            'grid_trading': {
                'grid_levels': 10,
                'grid_spacing_pct': 0.5,
                'position_size_per_grid': 50,
                'max_positions': 5,
                'range_period': 100,
                'take_profit_percent': 2,
                'stop_loss_percent': 5,
                'candle_interval': '15m',
                'range_pct_threshold': 10,
                'volatility_threshold': 0.15,
                'grid_recalc_bars': 20,
                'grid_saturation_threshold': 0.7,
                'grid_boundary_margin_low': 0.98,
                'grid_boundary_margin_high': 1.02,
                'account_cap_pct': 0.05,
            },
            'breakout': {
                'lookback_period': 20,
                'volume_multiplier': 1.5,
                'breakout_confirmation_bars': 2,
                'atr_period': 14,
                'position_size_usd': 100,
                'max_positions': 3,
                'take_profit_percent': 7,
                'stop_loss_percent': 3,
                'candle_interval': '15m',
                'pivot_window': 5,
                'avg_volume_lookback': 20,
                'stop_loss_atr_multiplier': 1.5,
                'position_stop_loss_atr_multiplier': 2.0,
                'strong_breakout_multiplier': 1.5,
                'high_atr_threshold': 3.0,
                'low_atr_threshold': 1.0,
                'high_atr_multiplier': 0.7,
                'low_atr_multiplier': 1.3,
            },
            'market_making': {
                'spread_bps': 5,
                'order_size_usd': 50,
                'max_open_orders': 4,
                'refresh_interval_seconds': 30,
                'close_immediately': True,
                'maker_only': False,
                'max_positions': 3,
                'take_profit_percent': 1,
                'stop_loss_percent': 2,
                'account_cap_pct': 0.05,
            }
        }

        # Merge: default config as base, CLI overrides on top
        config = {**default_configs.get(strategy_name, {}), **(strategy_config or {})}

        # Store strategy name and coins for validation
        self.strategy_name = strategy_name
        self.strategy_config = config

        # Build full coin list: standard HL coins + HIP-3 "dex:coin" coins
        hl_coins = coins or ['BTC', 'ETH'] if Config.ENABLE_STANDARD_HL else []
        hip3_coins: List[str] = []
        for dex in self.hip3_dexes:
            # Use per-DEX override if configured, else use discovered coins
            if dex in Config.DEX_COINS:
                dex_coin_names = Config.DEX_COINS[dex]
            else:
                dex_coin_names = self.registry.list_coins(dex)
            hip3_coins.extend(f"{dex}:{c}" for c in dex_coin_names)

        all_coins = (hl_coins if Config.ENABLE_STANDARD_HL else []) + hip3_coins
        self.trading_coins = all_coins or ['BTC', 'ETH']

        if hip3_coins:
            logger.info(f"HIP-3 coins: {hip3_coins}")
        logger.info(f"All trading coins: {self.trading_coins}")

        # Strategy factory
        strategy_map = {
            'simple_ma': SimpleMAStrategy,
            'rsi': RSIStrategy,
            'bollinger_bands': BollingerBandsStrategy,
            'macd': MACDStrategy,
            'grid_trading': GridTradingStrategy,
            'breakout': BreakoutStrategy,
            'market_making': MarketMakingStrategy
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

        self.coins = self.trading_coins

    def _load_wallet(self):
        from eth_account import Account
        return Account.from_key(Config.PRIVATE_KEY)

    @staticmethod
    def _build_risk_config() -> Dict:
        """Build a risk-manager config dict from :class:`Config` class attrs."""
        return {
            # Legacy parameters (fixed defaults for backwards compatibility)
            'max_leverage': 3.0,
            'max_position_size_pct': 0.2,
            'max_drawdown_pct': 0.1,
            'daily_loss_limit_pct': 0.05,
            # Configurable guardrails
            'max_position_pct': Config.MAX_POSITION_PCT,
            'max_margin_usage': Config.MAX_MARGIN_USAGE,
            'force_close_margin': Config.FORCE_CLOSE_MARGIN,
            'daily_loss_limit': Config.DAILY_LOSS_LIMIT,
            'per_trade_stop_loss': Config.PER_TRADE_STOP_LOSS,
            'max_open_positions': Config.MAX_OPEN_POSITIONS,
            'cooldown_after_stop': Config.COOLDOWN_AFTER_STOP,
        }

    def _build_perp_dexs(self) -> list:
        """Build the perp_dexs list expected by the SDK: '' for standard HL, named strings for HIP-3."""
        return ([""] if Config.ENABLE_STANDARD_HL else []) + self.hip3_dexes

    def get_user_state(self) -> Dict:
        try:
            from rate_limiter import api_wrapper
            user_state = api_wrapper.call(self.info.user_state, self.account_address)
            logger.info("User state retrieved successfully")
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
                time.sleep(self.main_loop_interval)

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
        action = risk_checks.get('action', 'none')

        if not risk_checks['all_checks_passed']:
            logger.warning(f"Risk limits exceeded: {risk_checks.get('reason')}")
            self.order_manager.cancel_all_orders()

            if action == 'stop_bot':
                logger.critical("Daily loss limit exceeded – stopping bot")
                self._close_all_positions()
                self.risk_manager.record_emergency_stop()
                self.running = False
                return

            if action == 'force_close':
                logger.warning("Force-close margin threshold breached – closing all positions")
                self._close_all_positions()
                self.risk_manager.record_emergency_stop()
                return

            if action == 'close_all':
                logger.warning("RISK_LEVEL=black – closing all positions")
                self._close_all_positions()
                return

            if action == 'block_new_orders':
                # Block new orders but continue managing existing positions
                self.order_manager.update_order_status()
                self._check_per_trade_stops()
                return

            # pause, cooldown: orders already cancelled above
            return

        self.order_manager.update_order_status()

        # Per-trade stop loss check (every cycle, if enabled)
        self._check_per_trade_stops()

        self.strategy.run(self.coins)

        if int(time.time()) % 60 == 0:
            risk_summary = self.risk_manager.get_risk_summary()
            logger.info(f"Risk summary: {risk_summary}")

    # ------------------------------------------------------------------ #
    #  Position management helpers
    # ------------------------------------------------------------------ #

    def _close_position(self, pos: Dict, reason: str = "") -> bool:
        """Market-close a single position. Returns True on success."""
        coin = pos.get('coin', '')
        size = float(pos.get('szi', 0))
        if size == 0:
            return False

        close_side = OrderSide.SELL if size > 0 else OrderSide.BUY
        abs_size = abs(size)

        sz_decimals = self.market_data.get_sz_decimals(coin)
        abs_size = round(abs_size, sz_decimals)

        order = self.order_manager.create_market_order(
            coin=coin,
            side=close_side,
            size=abs_size,
            reduce_only=True,
        )
        if order:
            prefix = f"{reason}: " if reason else ""
            logger.info(f"{prefix}Closed position for {coin}: size={abs_size}")
            return True
        else:
            logger.error(f"Failed to close position for {coin}")
            return False

    def _close_all_positions(self):
        """Market-close every open position."""
        try:
            positions = self.order_manager.get_all_positions()
            if not positions:
                logger.info("No open positions to close")
                return
            for pos in positions:
                self._close_position(pos)
        except Exception as e:
            logger.error(f"Error closing all positions: {e}")

    def _check_per_trade_stops(self):
        """Close individual positions that exceed the per-trade stop loss."""
        if self.risk_manager.per_trade_stop_loss is None:
            return
        try:
            positions = self.order_manager.get_all_positions()
            to_close = self.risk_manager.check_per_trade_stop_loss(positions)
            for pos in to_close:
                self._close_position(pos, reason="Per-trade stop loss")
        except Exception as e:
            logger.error(f"Error in per-trade stop loss check: {e}")

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
            if self.hip3_dexes:
                self.registry = DEXRegistry(Config.API_URL)
                self.registry.discover(self.hip3_dexes)
                self.exchange = Exchange(
                    wallet=self._load_wallet(),
                    base_url=Config.API_URL,
                    perp_dexs=self._build_perp_dexs(),
                )
                self.info = self.exchange.info
                self.market_data = MultiDexMarketData(self.info, self.registry, Config.API_URL)
                self.order_manager = MultiDexOrderManager(
                    exchange=self.exchange,
                    info=self.info,
                    account_address=self.account_address,
                    registry=self.registry,
                    market_data=self.market_data,
                    hip3_dexes=self.hip3_dexes,
                    default_slippage=self.market_order_slippage,
                )
            else:
                self.exchange = Exchange(
                    wallet=self._load_wallet(),
                    base_url=Config.API_URL,
                )
                self.info = self.exchange.info
                self.market_data = MarketDataManager(self.info)
                self.order_manager = OrderManager(self.exchange, self.info,
                                                  self.account_address, default_slippage=self.market_order_slippage)

            # Preserve cooldown state across connection resets
            prev_emergency_stop_time = self.risk_manager._emergency_stop_time
            prev_daily_starting_balance = self.risk_manager.daily_starting_balance
            self.risk_manager = RiskManager(self.info, self.account_address, self.risk_config)
            self.risk_manager._emergency_stop_time = prev_emergency_stop_time
            self.risk_manager.daily_starting_balance = prev_daily_starting_balance

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
    import os

    parser = argparse.ArgumentParser(description='Hyperliquid Trading Bot')
    parser.add_argument(
        '--strategy',
        type=str,
        default='simple_ma',
        choices=['simple_ma', 'rsi', 'bollinger_bands', 'macd', 'grid_trading', 'breakout', 'market_making'],
        help='Trading strategy to use'
    )
    parser.add_argument(
        '--coins',
        type=str,
        nargs='+',
        default=['BTC', 'ETH', 'SOL'],
        help='Coins to trade on standard Hyperliquid (e.g. BTC ETH SOL)'
    )
    parser.add_argument(
        '--dex',
        type=str,
        nargs='+',
        default=None,
        help=(
            'HIP-3 DEX names to trade on (e.g. xyz flx). '
            'Overrides TRADING_DEXES env var. '
            'Use XYZ_COINS / FLX_COINS env vars to set per-DEX coin lists.'
        )
    )
    parser.add_argument(
        '--no-hl',
        action='store_true',
        default=False,
        help='Disable trading on standard Hyperliquid perps (trade only HIP-3 DEXes)'
    )

    # Common strategy parameters
    parser.add_argument('--position-size-usd', type=float, help='Position size in USD')
    parser.add_argument('--max-positions', type=int, help='Maximum number of positions')
    parser.add_argument('--take-profit-percent', type=float, help='Take profit percentage')
    parser.add_argument('--stop-loss-percent', type=float, help='Stop loss percentage')
    parser.add_argument('--candle-interval', type=str, help='Candle interval (e.g. 1m, 5m, 15m, 1h)')
    parser.add_argument('--market-order-slippage', type=float, help='Market order slippage (default: 0.01 = 1%%)')
    parser.add_argument('--main-loop-interval', type=float, help='Main loop sleep interval in seconds (default: 10)')
    parser.add_argument('--account-cap-pct', type=float,
                        help='Max position as %% of account for sizing (grid_trading/market_making)')

    # Simple MA strategy parameters
    parser.add_argument('--fast-ma-period', type=int, help='Fast MA period (simple_ma)')
    parser.add_argument('--slow-ma-period', type=int, help='Slow MA period (simple_ma)')

    # RSI strategy parameters
    parser.add_argument('--rsi-period', type=int, help='RSI period (rsi)')
    parser.add_argument('--oversold-threshold', type=int, help='RSI oversold threshold (rsi)')
    parser.add_argument('--overbought-threshold', type=int, help='RSI overbought threshold (rsi)')
    parser.add_argument('--rsi-extreme-low', type=int, help='RSI extreme low for size increase (rsi, default: 25)')
    parser.add_argument('--rsi-moderate-low', type=int, help='RSI moderate low for size increase (rsi, default: 35)')
    parser.add_argument('--size-multiplier-extreme', type=float,
                        help='Size multiplier when RSI < extreme_low (rsi, default: 1.5)')
    parser.add_argument('--size-multiplier-moderate', type=float,
                        help='Size multiplier when RSI < moderate_low (rsi, default: 1.2)')

    # Bollinger Bands parameters
    parser.add_argument('--bb-period', type=int, help='Bollinger Bands period (bollinger_bands)')
    parser.add_argument('--std-dev', type=float, help='Standard deviation (bollinger_bands)')
    parser.add_argument('--squeeze-threshold', type=float, help='Squeeze threshold (bollinger_bands)')
    parser.add_argument('--volatility-expansion-threshold', type=float,
                        help='Volatility expansion multiplier (bollinger_bands, default: 1.5)')
    parser.add_argument('--high-band-width-threshold', type=float,
                        help='Band width threshold to reduce size (bollinger_bands, default: 0.05)')
    parser.add_argument('--high-band-width-multiplier', type=float,
                        help='Size multiplier when band width is high (bollinger_bands, default: 0.8)')
    parser.add_argument('--low-band-width-threshold', type=float,
                        help='Band width threshold to increase size (bollinger_bands, default: 0.02)')
    parser.add_argument('--low-band-width-multiplier', type=float,
                        help='Size multiplier when band width is low (bollinger_bands, default: 1.2)')

    # MACD parameters
    parser.add_argument('--fast-ema', type=int, help='Fast EMA period (macd)')
    parser.add_argument('--slow-ema', type=int, help='Slow EMA period (macd)')
    parser.add_argument('--signal-ema', type=int, help='Signal EMA period (macd)')
    parser.add_argument('--divergence-lookback', type=int, help='Divergence detection lookback (macd, default: 20)')
    parser.add_argument('--histogram-strength-high', type=float,
                        help='Histogram strength to increase size (macd, default: 0.5)')
    parser.add_argument('--histogram-strength-low', type=float,
                        help='Histogram strength to reduce size (macd, default: 0.1)')
    parser.add_argument('--histogram-multiplier-high', type=float,
                        help='Size multiplier for strong histogram (macd, default: 1.3)')
    parser.add_argument('--histogram-multiplier-low', type=float,
                        help='Size multiplier for weak histogram (macd, default: 0.7)')

    # Grid Trading parameters
    parser.add_argument('--grid-levels', type=int, help='Number of grid levels (grid_trading)')
    parser.add_argument('--grid-spacing-pct', type=float, help='Grid spacing percentage (grid_trading)')
    parser.add_argument('--position-size-per-grid', type=float, help='Position size per grid (grid_trading)')
    parser.add_argument('--range-period', type=int, help='Range period (grid_trading)')
    parser.add_argument('--range-pct-threshold', type=float,
                        help='Max range %% for ranging market (grid_trading, default: 10)')
    parser.add_argument('--volatility-threshold', type=float,
                        help='Max volatility for ranging market (grid_trading, default: 0.15)')
    parser.add_argument('--grid-recalc-bars', type=int,
                        help='Bars between grid recalculation (grid_trading, default: 20)')
    parser.add_argument('--grid-saturation-threshold', type=float,
                        help='Grid fill ratio to reduce size (grid_trading, default: 0.7)')
    parser.add_argument('--grid-boundary-margin-low', type=float,
                        help='Low boundary margin (grid_trading, default: 0.98)')
    parser.add_argument('--grid-boundary-margin-high', type=float,
                        help='High boundary margin (grid_trading, default: 1.02)')

    # Breakout parameters
    parser.add_argument('--lookback-period', type=int, help='Lookback period (breakout)')
    parser.add_argument('--volume-multiplier', type=float, help='Volume multiplier (breakout)')
    parser.add_argument('--breakout-confirmation-bars', type=int, help='Breakout confirmation bars (breakout)')
    parser.add_argument('--atr-period', type=int, help='ATR period (breakout)')
    parser.add_argument('--pivot-window', type=int, help='Pivot detection window (breakout, default: 5)')
    parser.add_argument('--avg-volume-lookback', type=int, help='Average volume lookback bars (breakout, default: 20)')
    parser.add_argument('--stop-loss-atr-multiplier', type=float,
                        help='Stop loss ATR multiplier (breakout, default: 1.5)')
    parser.add_argument('--position-stop-loss-atr-multiplier', type=float,
                        help='Position stop loss ATR multiplier (breakout, default: 2.0)')
    parser.add_argument('--strong-breakout-multiplier', type=float,
                        help='Size multiplier for strong breakout (breakout, default: 1.5)')
    parser.add_argument('--high-atr-threshold', type=float, help='ATR %% to reduce size (breakout, default: 3.0)')
    parser.add_argument('--low-atr-threshold', type=float, help='ATR %% to increase size (breakout, default: 1.0)')
    parser.add_argument('--high-atr-multiplier', type=float,
                        help='Size multiplier for high ATR (breakout, default: 0.7)')
    parser.add_argument('--low-atr-multiplier', type=float, help='Size multiplier for low ATR (breakout, default: 1.3)')

    # Market Making parameters
    parser.add_argument('--spread-bps', type=float,
                        help='Spread from mid price in basis points (market_making, default: 5)')
    parser.add_argument('--order-size-usd', type=float, help='Size per order in USD (market_making, default: 50)')
    parser.add_argument('--max-open-orders', type=int, help='Max concurrent open orders (market_making, default: 4)')
    parser.add_argument('--refresh-interval', type=float,
                        help='Seconds before cancelling stale orders (market_making, default: 30)')
    parser.add_argument('--no-close-immediately', action='store_true', default=False,
                        help='Disable immediate position closing (market_making)')
    parser.add_argument('--max-position-age', type=float,
                        help='Max seconds to hold a position before force-closing (market_making, default: 120)')
    parser.add_argument('--maker-only', action='store_true', default=False,
                        help='Use post-only (maker) orders for all trades including closes (market_making)')
    parser.add_argument('--taker-fallback-age', type=float,
                        help='Seconds after max-position-age to fall back to taker for force-close. '
                             'Not set = never use taker. 0 = taker at max-position-age. (market_making)')

    # Risk guardrail parameters
    parser.add_argument('--max-position-pct', type=float,
                        help='Max single position as %% of account (default: 0.2)')
    parser.add_argument('--max-margin-usage', type=float,
                        help='Stop new orders above this margin usage (default: 0.8)')
    parser.add_argument('--force-close-margin', type=float,
                        help='Force close ALL positions above this margin ratio (disabled by default)')
    parser.add_argument('--daily-loss-limit', type=float,
                        help='Absolute $ daily loss to auto-stop the bot (disabled by default)')
    parser.add_argument('--per-trade-stop-loss', type=float,
                        help='Cut losing trades at this %% loss (e.g. 0.05 = 5%%, disabled by default)')
    parser.add_argument('--max-open-positions', type=int,
                        help='Max concurrent open positions (default: 5)')
    parser.add_argument('--cooldown-after-stop', type=int,
                        help='Seconds to wait after emergency stop (default: 3600)')
    parser.add_argument('--risk-level', type=str,
                        choices=['green', 'yellow', 'red', 'black'],
                        help='Dynamic risk level: green=100%%, yellow=50%%, red=pause, black=close all')

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
    if args.candle_interval is not None:
        strategy_config['candle_interval'] = args.candle_interval
    if args.account_cap_pct is not None:
        strategy_config['account_cap_pct'] = args.account_cap_pct

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
        if args.rsi_extreme_low is not None:
            strategy_config['rsi_extreme_low'] = args.rsi_extreme_low
        if args.rsi_moderate_low is not None:
            strategy_config['rsi_moderate_low'] = args.rsi_moderate_low
        if args.size_multiplier_extreme is not None:
            strategy_config['size_multiplier_extreme'] = args.size_multiplier_extreme
        if args.size_multiplier_moderate is not None:
            strategy_config['size_multiplier_moderate'] = args.size_multiplier_moderate

    elif args.strategy == 'bollinger_bands':
        if args.bb_period is not None:
            strategy_config['bb_period'] = args.bb_period
        if args.std_dev is not None:
            strategy_config['std_dev'] = args.std_dev
        if args.squeeze_threshold is not None:
            strategy_config['squeeze_threshold'] = args.squeeze_threshold
        if args.volatility_expansion_threshold is not None:
            strategy_config['volatility_expansion_threshold'] = args.volatility_expansion_threshold
        if args.high_band_width_threshold is not None:
            strategy_config['high_band_width_threshold'] = args.high_band_width_threshold
        if args.high_band_width_multiplier is not None:
            strategy_config['high_band_width_multiplier'] = args.high_band_width_multiplier
        if args.low_band_width_threshold is not None:
            strategy_config['low_band_width_threshold'] = args.low_band_width_threshold
        if args.low_band_width_multiplier is not None:
            strategy_config['low_band_width_multiplier'] = args.low_band_width_multiplier

    elif args.strategy == 'macd':
        if args.fast_ema is not None:
            strategy_config['fast_ema'] = args.fast_ema
        if args.slow_ema is not None:
            strategy_config['slow_ema'] = args.slow_ema
        if args.signal_ema is not None:
            strategy_config['signal_ema'] = args.signal_ema
        if args.divergence_lookback is not None:
            strategy_config['divergence_lookback'] = args.divergence_lookback
        if args.histogram_strength_high is not None:
            strategy_config['histogram_strength_high'] = args.histogram_strength_high
        if args.histogram_strength_low is not None:
            strategy_config['histogram_strength_low'] = args.histogram_strength_low
        if args.histogram_multiplier_high is not None:
            strategy_config['histogram_multiplier_high'] = args.histogram_multiplier_high
        if args.histogram_multiplier_low is not None:
            strategy_config['histogram_multiplier_low'] = args.histogram_multiplier_low

    elif args.strategy == 'grid_trading':
        if args.grid_levels is not None:
            strategy_config['grid_levels'] = args.grid_levels
        if args.grid_spacing_pct is not None:
            strategy_config['grid_spacing_pct'] = args.grid_spacing_pct
        if args.position_size_per_grid is not None:
            strategy_config['position_size_per_grid'] = args.position_size_per_grid
        if args.range_period is not None:
            strategy_config['range_period'] = args.range_period
        if args.range_pct_threshold is not None:
            strategy_config['range_pct_threshold'] = args.range_pct_threshold
        if args.volatility_threshold is not None:
            strategy_config['volatility_threshold'] = args.volatility_threshold
        if args.grid_recalc_bars is not None:
            strategy_config['grid_recalc_bars'] = args.grid_recalc_bars
        if args.grid_saturation_threshold is not None:
            strategy_config['grid_saturation_threshold'] = args.grid_saturation_threshold
        if args.grid_boundary_margin_low is not None:
            strategy_config['grid_boundary_margin_low'] = args.grid_boundary_margin_low
        if args.grid_boundary_margin_high is not None:
            strategy_config['grid_boundary_margin_high'] = args.grid_boundary_margin_high

    elif args.strategy == 'breakout':
        if args.lookback_period is not None:
            strategy_config['lookback_period'] = args.lookback_period
        if args.volume_multiplier is not None:
            strategy_config['volume_multiplier'] = args.volume_multiplier
        if args.breakout_confirmation_bars is not None:
            strategy_config['breakout_confirmation_bars'] = args.breakout_confirmation_bars
        if args.atr_period is not None:
            strategy_config['atr_period'] = args.atr_period
        if args.pivot_window is not None:
            strategy_config['pivot_window'] = args.pivot_window
        if args.avg_volume_lookback is not None:
            strategy_config['avg_volume_lookback'] = args.avg_volume_lookback
        if args.stop_loss_atr_multiplier is not None:
            strategy_config['stop_loss_atr_multiplier'] = args.stop_loss_atr_multiplier
        if args.position_stop_loss_atr_multiplier is not None:
            strategy_config['position_stop_loss_atr_multiplier'] = args.position_stop_loss_atr_multiplier
        if args.strong_breakout_multiplier is not None:
            strategy_config['strong_breakout_multiplier'] = args.strong_breakout_multiplier
        if args.high_atr_threshold is not None:
            strategy_config['high_atr_threshold'] = args.high_atr_threshold
        if args.low_atr_threshold is not None:
            strategy_config['low_atr_threshold'] = args.low_atr_threshold
        if args.high_atr_multiplier is not None:
            strategy_config['high_atr_multiplier'] = args.high_atr_multiplier
        if args.low_atr_multiplier is not None:
            strategy_config['low_atr_multiplier'] = args.low_atr_multiplier

    elif args.strategy == 'market_making':
        if args.spread_bps is not None:
            strategy_config['spread_bps'] = args.spread_bps
        if args.order_size_usd is not None:
            strategy_config['order_size_usd'] = args.order_size_usd
        if args.max_open_orders is not None:
            strategy_config['max_open_orders'] = args.max_open_orders
        if args.refresh_interval is not None:
            strategy_config['refresh_interval_seconds'] = args.refresh_interval
        if args.no_close_immediately:
            strategy_config['close_immediately'] = False
        if hasattr(args, 'max_position_age') and args.max_position_age is not None:
            strategy_config['max_position_age_seconds'] = args.max_position_age
        if hasattr(args, 'maker_only') and args.maker_only:
            strategy_config['maker_only'] = True
        if hasattr(args, 'taker_fallback_age') and args.taker_fallback_age is not None:
            strategy_config['taker_fallback_age_seconds'] = args.taker_fallback_age

    # Apply CLI overrides for DEX settings
    if args.dex is not None:
        os.environ["TRADING_DEXES"] = ",".join(args.dex)
        # Reload config after env change
        Config.TRADING_DEXES = args.dex
    if args.no_hl:
        Config.ENABLE_STANDARD_HL = False

    # Apply CLI overrides for risk guardrails (CLI > env > default)
    if args.max_position_pct is not None:
        Config.MAX_POSITION_PCT = args.max_position_pct
    if args.max_margin_usage is not None:
        Config.MAX_MARGIN_USAGE = args.max_margin_usage
    if args.force_close_margin is not None:
        Config.FORCE_CLOSE_MARGIN = args.force_close_margin
    if args.daily_loss_limit is not None:
        Config.DAILY_LOSS_LIMIT = args.daily_loss_limit
    if args.per_trade_stop_loss is not None:
        Config.PER_TRADE_STOP_LOSS = args.per_trade_stop_loss
    if args.max_open_positions is not None:
        Config.MAX_OPEN_POSITIONS = args.max_open_positions
    if args.cooldown_after_stop is not None:
        Config.COOLDOWN_AFTER_STOP = args.cooldown_after_stop
    if args.risk_level is not None:
        os.environ["RISK_LEVEL"] = args.risk_level

    logger.info(f"Starting bot with {args.strategy} strategy")
    logger.info(f"Standard HL coins: {args.coins if Config.ENABLE_STANDARD_HL else '(disabled)'}")
    if Config.TRADING_DEXES:
        logger.info(f"HIP-3 DEXes: {Config.TRADING_DEXES}")
    if strategy_config:
        logger.info(f"Custom parameters: {json.dumps(strategy_config, indent=2)}")

    bot = HyperliquidBot(
        strategy_name=args.strategy,
        coins=args.coins if Config.ENABLE_STANDARD_HL else [],
        strategy_config=strategy_config if strategy_config else None,
        main_loop_interval=args.main_loop_interval if args.main_loop_interval is not None else 10,
        market_order_slippage=args.market_order_slippage if args.market_order_slippage is not None else 0.01,
    )
    bot.run()
