import logging
import os
import sys
import time
import signal
import types
import warnings
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any, Dict, List, Optional, Tuple

from log_config import setup_logging
setup_logging()

# ---------------------------------------------------------------------------
# SDK compatibility gate
# ---------------------------------------------------------------------------
# Hyperliquid mainnet metadata evolves over time. The installed
# ``hyperliquid-python-sdk`` must be at least this version to handle the
# current spot universe correctly. When upgrading the SDK, bump this constant
# in the same PR so deployed environments are validated at startup.
MINIMUM_HYPERLIQUID_SDK_VERSION: Tuple[int, int, int] = (0, 23, 0)


def _parse_version_tuple(version: str) -> Tuple[int, int, int]:
    """Parse a version string into a comparable ``MAJOR.MINOR.PATCH`` tuple.

    Only the first three numeric components are considered. Pre-release or
    development suffixes (e.g. ``rc1``, ``a2``) on any component are stripped
    so that ``0.23.0rc1`` is treated as ``(0, 23, 0)``. Missing components
    default to 0 (``"1.0"`` becomes ``(1, 0, 0)``).
    """
    parts = version.split(".")
    nums: List[int] = []
    for part in parts[:3]:
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _assert_sdk_version() -> None:
    """Abort startup if ``hyperliquid-python-sdk`` is below the minimum.

    Exits with status 2 (configuration error) and a clear remediation message
    so a crash loop in production can be diagnosed from a single log line
    rather than a deep API traceback.
    """
    pkg = "hyperliquid-python-sdk"
    try:
        installed = _pkg_version(pkg)
    except PackageNotFoundError:
        min_str = ".".join(str(n) for n in MINIMUM_HYPERLIQUID_SDK_VERSION)
        sys.stderr.write(
            f"FATAL: {pkg} is not installed but is required (>= {min_str}). "
            f"Run `pip install \".[dev]\"` from the bot repo to install all "
            f"dependencies.\n"
        )
        sys.exit(2)

    if _parse_version_tuple(installed) < MINIMUM_HYPERLIQUID_SDK_VERSION:
        min_str = ".".join(str(n) for n in MINIMUM_HYPERLIQUID_SDK_VERSION)
        sys.stderr.write(
            f"FATAL: {pkg} {installed} is installed, but minimum required "
            f"version is {min_str}. The bot will not function correctly with "
            f"this SDK due to upstream API metadata changes.\n"
            f"To fix: `pip install \".[dev]\"` from the bot repo "
            f"(or `pip install --upgrade '{pkg}>={min_str}'`).\n"
        )
        sys.exit(2)


_assert_sdk_version()

from hyperliquid.exchange import Exchange  # noqa: E402
from config import Config  # noqa: E402
from market_data import MarketDataManager  # noqa: E402
from order_manager import OrderManager  # noqa: E402
from risk_manager import RiskManager  # noqa: E402
from validation import MarginValidator, validate_strategy_config  # noqa: E402
from validation.strategy_validator import known_market_making_keys  # noqa: E402
from json_config_loader import ConfigError, load_json_configs  # noqa: E402
from hip3 import DEXRegistry, MultiDexMarketData, MultiDexOrderManager  # noqa: E402
from position_closer import close_position_market  # noqa: E402
from rate_limiter import API_ERRORS  # noqa: E402
from exceptions import TransientError, DataError, ConfigurationError  # noqa: E402
from circuit_breaker import CircuitBreaker  # noqa: E402
from ws import (  # noqa: E402
    MarketDataFeed, FillFeed, BboGuard, ImbalanceGuard,
    CloseRefreshGuard, BboVelocityGuard, AdverseSelectionTracker, WsReconnector,
)
from strategies import (  # noqa: E402
    SimpleMAStrategy,
    RSIStrategy,
    BollingerBandsStrategy,
    MACDStrategy,
    GridTradingStrategy,
    BreakoutStrategy,
    MarketMakingStrategy
)

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

logger = logging.getLogger(__name__)


# Risk-guardrail parameter names. Promoted to module-level so the JSON
# config layer (``_apply_json_risk_overrides``) and the existing CLI
# override loop in ``main()`` share the same list. Each name maps to:
#   - argparse dest: ``args.{name}``
#   - Config class attribute: ``Config.{name.upper()}``
_RISK_PARAMS = [
    'max_position_pct', 'max_margin_usage', 'force_close_margin',
    'force_close_leverage', 'daily_loss_limit', 'per_trade_stop_loss',
    'max_open_positions', 'cooldown_after_stop',
]

# Common-strategy parameter names (apply to most / all strategies).
# Module-level so ``validation.strategy_validator.known_market_making_keys()``
# can introspect them for typo detection without circular-import gymnastics.
_COMMON_PARAMS = [
    'position_size_usd', 'max_positions', 'take_profit_percent',
    'stop_loss_percent', 'candle_interval', 'account_cap_pct',
]

# Per-strategy parameter names. Each list contains the ``config_key``
# (i.e. the name produced by argparse ``dest=``); for renames where the
# CLI flag and config key differ, ``_collect_params`` accepts an
# ``(arg_name, config_key)`` tuple. Module-level for the same reason as
# ``_COMMON_PARAMS`` above.
_STRATEGY_PARAMS = {
    'simple_ma': ['fast_ma_period', 'slow_ma_period'],
    'rsi': [
        'rsi_period', 'oversold_threshold', 'overbought_threshold',
        'rsi_extreme_low', 'rsi_moderate_low',
        'size_multiplier_extreme', 'size_multiplier_moderate',
    ],
    'bollinger_bands': [
        'bb_period', 'std_dev', 'squeeze_threshold',
        'volatility_expansion_threshold',
        'high_band_width_threshold', 'high_band_width_multiplier',
        'low_band_width_threshold', 'low_band_width_multiplier',
    ],
    'macd': [
        'fast_ema', 'slow_ema', 'signal_ema', 'divergence_lookback',
        'histogram_strength_high', 'histogram_strength_low',
        'histogram_multiplier_high', 'histogram_multiplier_low',
    ],
    'grid_trading': [
        'grid_levels', 'grid_spacing_pct', 'position_size_per_grid',
        'range_period', 'range_pct_threshold', 'volatility_threshold',
        'grid_recalc_bars', 'grid_saturation_threshold',
        'grid_boundary_margin_low', 'grid_boundary_margin_high',
    ],
    'breakout': [
        'lookback_period', 'volume_multiplier',
        'breakout_confirmation_bars', 'atr_period',
        'pivot_window', 'avg_volume_lookback',
        'stop_loss_atr_multiplier', 'position_stop_loss_atr_multiplier',
        'strong_breakout_multiplier',
        'high_atr_threshold', 'low_atr_threshold',
        'high_atr_multiplier', 'low_atr_multiplier',
    ],
    'market_making': [
        'spread_bps', 'order_size_usd', 'max_open_orders',
        'refresh_interval_seconds',
        'refresh_tolerance_bp',
        'refresh_max_age_seconds',
        'max_position_age_seconds',
        'taker_fallback_age_seconds',
        'aggressive_loss_bps',
        'force_close_max_loss_bps',
        'close_spread_bps',
        'close_breakeven_pct',
        'close_aggressive_pct',
        'unrealized_loss_close_bps',
        'bbo_mode',
        'bbo_offset_bps',
        'inventory_skew_bps',
        'imbalance_threshold',
        'loss_streak_limit',
        'loss_streak_cooldown',
        'bbo_guard_threshold_bps',
        'imbalance_guard_threshold',
        'imbalance_guard_depth',
        'vol_adjust_enabled',
        'vol_adjust_multiplier',
        'vol_lookback',
        'vol_adjust_max_offset',
        'adverse_selection_log_interval',
        'coin_offset_overrides',
        'coin_spread_overrides',
        'coin_size_overrides',
        'coin_unrealized_loss_overrides',
        'close_refresh_threshold_bps',
        'spread_schedule',
        'quiet_hours_utc',
        'quiet_hours_spread_multiplier',
        'microprice_skew_enabled',
        'microprice_skew_multiplier',
        'microprice_max_skew_bps',
        'velocity_guard_enabled',
        'velocity_consecutive',
        'velocity_min_move_bps',
        'dynamic_offset_enabled',
        'dynamic_offset_sensitivity',
        'dynamic_offset_tighten_rate',
        'dynamic_offset_max_addition',
        'dynamic_offset_max_reduction',
        'dynamic_offset_floor',
        'dynamic_offset_min_fills',
        'dynamic_age_enabled',
        'dynamic_age_baseline_vol',
        'dynamic_age_min',
        'dynamic_age_max',
        'auto_exclude_enabled',
        'auto_exclude_threshold_bps',
        'auto_exclude_consecutive',
        'auto_exclude_min_fills',
        'auto_exclude_cooldown',
        'auto_exclude_window_label',
        'forager_enabled',
        'forager_score_threshold',
        'forager_consecutive',
        'forager_cooldown_seconds',
        'forager_weight_activity',
        'forager_weight_quality',
        'forager_weight_cost',
        'rejection_log_level',
        'rejection_summary_interval',
        'drain_flag_file',
        'max_position_multiple',
    ],
}


def _apply_json_risk_overrides(json_overrides: Optional[Dict], args) -> None:
    """Wire risk parameters from JSON config into the ``Config`` class.

    JSON values flow to ``Config.{KEY.upper()}`` only when the same key
    is **not** also present on ``args`` (CLI > JSON precedence). After
    application the keys are popped from ``json_overrides`` so they do
    not leak into the strategy_config dict (where the strategy would
    not know what to do with them and the typo detector would false-warn).

    No-op when ``json_overrides`` is empty or None. Existing CLI / env
    behaviour is unaffected.
    """
    if not json_overrides:
        return
    for param in _RISK_PARAMS:
        if param not in json_overrides:
            continue
        cli_val = getattr(args, param, None)
        if cli_val is not None:
            # CLI already specified — let the existing CLI loop handle it
            # and just pop the JSON value so it does not pollute strategy_config.
            json_overrides.pop(param, None)
            continue
        value = json_overrides.pop(param)
        setattr(Config, param.upper(), value)
        logger.info(f"[config] Risk param from JSON: {param.upper()}={value}")


class HyperliquidBot:
    def __init__(self, strategy_name: str = "simple_ma", coins: Optional[List[str]] = None,
                 strategy_config: Optional[Dict] = None,
                 json_overrides: Optional[Dict] = None,
                 main_loop_interval: float = 10, market_order_slippage: float = 0.01,
                 enable_ws: bool = False) -> None:
        Config.validate()
        self.account_address = Config.ACCOUNT_ADDRESS
        self.running = False
        self.connection_retry_count = 0
        self.last_connection_reset = time.time()
        self.main_loop_interval = main_loop_interval
        self.market_order_slippage = market_order_slippage
        self.api_timeout = Config.API_TIMEOUT
        self.circuit_breaker = CircuitBreaker(threshold=5, recovery_seconds=60.0)

        # Risk check throttling: avoid calling check_risk_limits every cycle
        # when main_loop_interval is short (e.g. 3s). Risk checks cost 4 weight
        # and don't need sub-10s frequency.
        self._risk_check_interval: float = Config.RISK_CHECK_INTERVAL
        # epoch 0 ensures the first cycle always triggers a risk check
        self._last_risk_check: float = 0.0
        # Fail-safe: default to blocking until first real check completes
        self._last_risk_result: dict = {'all_checks_passed': False, 'action': 'none'}
        self._enable_ws = enable_ws
        self.ws_feed: Optional[MarketDataFeed] = None
        self.fill_feed: Optional[FillFeed] = None
        self.bbo_guard: Optional[BboGuard] = None
        self.imbalance_guard: Optional[ImbalanceGuard] = None
        self.close_refresh_guard: Optional[CloseRefreshGuard] = None
        self.velocity_guard: Optional[BboVelocityGuard] = None
        self.adverse_tracker: Optional[AdverseSelectionTracker] = None
        self._ws_reconnector: Optional[WsReconnector] = None

        # ------------------------------------------------------------------ #
        # HIP-3 Multi-DEX setup
        # ------------------------------------------------------------------ #
        self.hip3_dexes: List[str] = Config.TRADING_DEXES
        self._init_connections()

        self.risk_config = self._build_risk_config()
        self.risk_manager = RiskManager(
            self.info, self.account_address, self.risk_config,
            hip3_dexes=self.hip3_dexes,
            market_data=self.market_data if self.hip3_dexes else None,
        )

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
                'refresh_tolerance_bp': 0,
                'close_immediately': True,
                'maker_only': False,
                'max_positions': 3,
                'take_profit_percent': 1,
                'stop_loss_percent': 2,
                'account_cap_pct': 0.05,
                # Forager (composite-score auto-exclude). Defaults match the
                # ForagerConfig dataclass; values can be overridden via env
                # vars (e.g. FORAGER_WINDOW_SECONDS=3600) without adding a
                # CLI flag for every internal formula constant.
                'forager_enabled': False,
                'forager_score_threshold': 30.0,
                'forager_consecutive': 3,
                'forager_cooldown_seconds': 1800,
                'forager_weight_activity': 0.3,
                'forager_weight_quality': 0.4,
                'forager_weight_cost': 0.3,
                'forager_window_seconds': 1800.0,
                'forager_check_interval_seconds': 300.0,
                'forager_activity_idle_min_seconds': 300.0,
                'forager_cost_max_per_1k': 0.6,
                'forager_min_closes_for_quality': 5,
                # Per-coin entry-side position cap. When set, suppresses
                # same-direction entries once |position| * mid_price reaches
                # ``max_position_multiple`` × effective ``order_size_usd``.
                # ``0.0`` (default) disables the cap.
                'max_position_multiple': 0.0,
            }
        }

        # Merge layers (lowest precedence first):
        #   dataclass defaults < JSON overrides < CLI / env (strategy_config).
        # JSON is opt-in via --config / $BOT_CONFIG; when both unset,
        # ``json_overrides`` is None and the layering matches prior releases.
        config = {
            **default_configs.get(strategy_name, {}),
            **(json_overrides or {}),
            **(strategy_config or {}),
        }

        # Validate strategy parameters early
        validation_error = validate_strategy_config(strategy_name, config)
        if validation_error:
            raise ValueError(validation_error)

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

        unique_coins, duplicates = self._deduplicate_coins(all_coins)
        if duplicates:
            logger.warning(f"Duplicate coins removed from trading list: {duplicates}")

        self.trading_coins = unique_coins or ['BTC', 'ETH']

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

    @staticmethod
    def _deduplicate_coins(coins: List[str]) -> tuple:
        """Remove duplicate coins while preserving order.

        Returns (unique_coins, duplicates) tuple.
        """
        seen: set = set()
        unique: List[str] = []
        dups: List[str] = []
        for coin in coins:
            if coin in seen:
                dups.append(coin)
            else:
                seen.add(coin)
                unique.append(coin)
        return unique, dups

    def _load_wallet(self) -> Any:
        from eth_account import Account
        return Account.from_key(Config.PRIVATE_KEY)

    def _init_connections(self) -> None:
        """Create Exchange, Info, MarketData, and OrderManager instances.

        Handles both standard Hyperliquid and HIP-3 multi-DEX modes.
        Called from ``__init__`` and ``_reset_connections``.
        """
        self.registry = DEXRegistry(Config.API_URL)

        if self.hip3_dexes:
            self.registry.discover(self.hip3_dexes)
            self.exchange = Exchange(
                wallet=self._load_wallet(),
                base_url=Config.API_URL,
                perp_dexs=self._build_perp_dexs(),
                timeout=self.api_timeout,
            )
            self.info = self.exchange.info
            logger.info("Registered DEXes:\n" + self.registry.summary())
            self.market_data = MultiDexMarketData(
                self.info, self.registry, Config.API_URL,
                meta_cache_ttl=Config.META_CACHE_TTL,
            )
            self.order_manager = MultiDexOrderManager(
                exchange=self.exchange,
                info=self.info,
                account_address=self.account_address,
                registry=self.registry,
                market_data=self.market_data,
                hip3_dexes=self.hip3_dexes,
                default_slippage=self.market_order_slippage,
                mids_cache_ttl=Config.MIDS_CACHE_TTL,
            )
        else:
            self.exchange = Exchange(
                wallet=self._load_wallet(),
                base_url=Config.API_URL,
                timeout=self.api_timeout,
            )
            self.info = self.exchange.info
            self.market_data = MarketDataManager(self.info, meta_cache_ttl=Config.META_CACHE_TTL)
            self.order_manager = OrderManager(
                self.exchange, self.info, self.account_address,
                default_slippage=self.market_order_slippage,
                mids_cache_ttl=Config.MIDS_CACHE_TTL,
            )

        # Pre-approve any per-DEX builder codes (e.g. for HIP-3 deployers
        # whose rewards programs require attaching a builder to each
        # order).  Idempotent — safe to re-run on every startup.
        self.order_manager.approve_configured_builders()

    @staticmethod
    def _build_risk_config() -> Dict:
        """Build a risk-manager config dict from :class:`Config` class attrs."""
        return {
            # Legacy parameters (fixed defaults for backwards compatibility)
            'max_leverage': 3.0,
            'max_drawdown_pct': 0.1,
            'daily_loss_limit_pct': 0.05,
            # Configurable guardrails
            'max_position_pct': Config.MAX_POSITION_PCT,
            'max_margin_usage': Config.MAX_MARGIN_USAGE,
            'force_close_margin': Config.FORCE_CLOSE_MARGIN,
            'force_close_leverage': Config.FORCE_CLOSE_LEVERAGE,
            'daily_loss_limit': Config.DAILY_LOSS_LIMIT,
            'per_trade_stop_loss': Config.PER_TRADE_STOP_LOSS,
            'max_open_positions': Config.MAX_OPEN_POSITIONS,
            'cooldown_after_stop': Config.COOLDOWN_AFTER_STOP,
            'metrics_cache_ttl': Config.METRICS_CACHE_TTL,
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
        except API_ERRORS as e:
            logger.error(f"Error getting user state for {self.account_address}: {e}")
            return {}

    def run(self) -> None:
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

        # Reset any rate-limiter backoff accumulated during validation.
        # Validation makes many API calls and can trigger 429s whose
        # backoff would otherwise penalise the first trading cycles.
        from rate_limiter import api_wrapper
        api_wrapper.rate_limiter.reset_backoff()

        # Start WebSocket market data feed (optional, non-blocking).
        # Exchange creates Info with skip_ws=True, so we need a separate
        # Info instance with WebSocket enabled for the feed.
        if self._enable_ws:
            from hyperliquid.info import Info as WsInfo
            perp_dexs = self._build_perp_dexs()
            ws_info = WsInfo(
                base_url=Config.API_URL,
                skip_ws=False,
                perp_dexs=perp_dexs,
                timeout=self.api_timeout,
            )
            self.ws_feed = MarketDataFeed(ws_info, self.market_data, self.coins)
            self.ws_feed.start()

            # Phase 2: instant fill detection → opposite-side cancel
            tracker = getattr(self.strategy, 'order_tracker', None)
            if tracker is not None:
                self.fill_feed = FillFeed(ws_info, tracker, self.account_address)
                # Connect PositionCloser for close-fill cleanup (prevents reduce-only rejections)
                closer = getattr(self.strategy, '_closer', None)
                if closer is not None:
                    self.fill_feed.set_position_closer(closer)
                self.fill_feed.start()

                # Phase 3: BBO change detection → stale quote cancel
                threshold = self.strategy_config.get('bbo_guard_threshold_bps', 2.0)
                if threshold > 0:
                    self.bbo_guard = BboGuard(
                        tracker,
                        threshold_bps=threshold,
                    )
                    self.ws_feed.add_listener(self.bbo_guard.on_l2_update)
                    logger.info("[ws] BboGuard enabled (threshold=%.1f bps)", threshold)

                # MM-strategy grouped config (None for non-MM strategies); each
                # WS guard below reads from it when available, with a fallback
                # to the flat strategy_config dict.
                strategy_cfg = getattr(self.strategy, 'cfg', None)

                # Phase 4: L2 imbalance detection → one-sided cancel
                if strategy_cfg is not None:
                    imb_threshold = strategy_cfg.imbalance.reactive_threshold
                    imb_depth = strategy_cfg.imbalance.reactive_depth
                else:
                    imb_threshold = float(self.strategy_config.get('imbalance_guard_threshold', 0))
                    imb_depth = int(self.strategy_config.get('imbalance_guard_depth', 5))
                if imb_threshold > 0:
                    self.imbalance_guard = ImbalanceGuard(
                        tracker,
                        threshold=imb_threshold,
                        depth=imb_depth,
                    )
                    self.ws_feed.add_listener(self.imbalance_guard.on_l2_update)
                    logger.info(
                        "[ws] ImbalanceGuard enabled (threshold=%.2f, depth=%d)",
                        imb_threshold, self.imbalance_guard.depth,
                    )

                # Phase 4b: BBO velocity → directional cancel
                if strategy_cfg is not None:
                    velocity_enabled = strategy_cfg.velocity.enabled
                    velocity_consecutive = strategy_cfg.velocity.consecutive
                    velocity_min_move = strategy_cfg.velocity.min_move_bps
                else:
                    velocity_enabled = bool(self.strategy_config.get('velocity_guard_enabled', False))
                    velocity_consecutive = int(self.strategy_config.get('velocity_consecutive', 3))
                    velocity_min_move = float(self.strategy_config.get('velocity_min_move_bps', 1.0))
                if velocity_enabled:
                    self.velocity_guard = BboVelocityGuard(
                        tracker,
                        consecutive_threshold=velocity_consecutive,
                        min_total_move_bps=velocity_min_move,
                    )
                    self.ws_feed.add_listener(self.velocity_guard.on_l2_update)
                    logger.info(
                        "[ws] BboVelocityGuard enabled (consecutive=%d, min_move=%.1f bps)",
                        self.velocity_guard.consecutive_threshold,
                        self.velocity_guard.min_total_move_bps,
                    )

                # Phase 5: Close order refresh on BBO change
                closer = getattr(self.strategy, '_closer', None)
                if strategy_cfg is not None:
                    close_refresh_threshold = strategy_cfg.close.refresh_threshold_bps
                else:
                    close_refresh_threshold = float(
                        self.strategy_config.get('close_refresh_threshold_bps', 0.0)
                    )
                if closer is not None and close_refresh_threshold > 0:
                    self.close_refresh_guard = CloseRefreshGuard(
                        closer,
                        threshold_bps=close_refresh_threshold,
                        min_refresh_interval=3.0,
                    )
                    self.ws_feed.add_listener(self.close_refresh_guard.on_l2_update)
                    logger.info(
                        "[ws] CloseRefreshGuard enabled (threshold=%.1f bps)",
                        close_refresh_threshold,
                    )

                # Phase 6: Adverse selection measurement (observation only)
                if self.strategy_config.get('enable_adverse_selection_log', False):
                    self.adverse_tracker = AdverseSelectionTracker(
                        market_data=self.market_data,
                        log_interval=self.strategy_config.get('adverse_selection_log_interval', 300.0),
                    )
                    self.fill_feed.set_adverse_selection_tracker(self.adverse_tracker)
                    logger.info("[ws] AdverseSelectionTracker enabled")

        # Inject adverse tracker into strategy for dynamic offset
        if self.adverse_tracker and self.strategy_config.get('dynamic_offset_enabled', False):
            self.strategy._adverse_tracker = self.adverse_tracker
            logger.info("[ws] Dynamic offset linked to AdverseSelectionTracker")

        # Forager: route fill events from FillFeed into the strategy's
        # CoinHealthTracker so the quality + cost dimensions are populated.
        coin_health_tracker = getattr(self.strategy, '_coin_health_tracker', None)
        if (
            self.fill_feed is not None
            and coin_health_tracker is not None
            and self.strategy_config.get('forager_enabled', False)
        ):
            self.fill_feed.set_coin_health_tracker(coin_health_tracker)
            logger.info("[ws] Forager linked to FillFeed (CoinHealthTracker)")

        if self._enable_ws and self.ws_feed is not None:
            self._ws_reconnector = WsReconnector(stale_threshold=60.0)

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

                # Check WebSocket health and reconnect if needed
                if self._ws_reconnector is not None:
                    self._ws_reconnector.maybe_reconnect(self)

                self._trading_loop()
                consecutive_errors = 0  # Reset on successful iteration
                time.sleep(self.main_loop_interval)

            except TransientError as e:
                consecutive_errors += 1
                wait = min(consecutive_errors * 5, 60)
                logger.error(f"Transient error (#{consecutive_errors}), retry in {wait}s: {e}")
                time.sleep(wait)

            except (DataError, ConfigurationError) as e:
                consecutive_errors += 1
                logger.error(f"Non-transient error (#{consecutive_errors}): {e}")
                time.sleep(10)

            except ConnectionError as e:
                consecutive_errors += 1
                wait = min(consecutive_errors * 5, 60)
                logger.error(f"Connection error (#{consecutive_errors}): {e}")
                time.sleep(wait)

            except KeyboardInterrupt:
                logger.info("Stopping bot...")
                self.running = False

            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in main loop (#{consecutive_errors}): {e}")
                time.sleep(10)

    def _trading_loop(self) -> None:
        # Throttle risk checks to avoid burning API weight every cycle.
        # With MAIN_LOOP_INTERVAL=3s, checking every 30s saves ~36 weight/min.
        now = time.time()
        if now - self._last_risk_check >= self._risk_check_interval:
            self._last_risk_result = self.risk_manager.check_risk_limits()
            self._last_risk_check = now
        risk_checks = self._last_risk_result
        action = risk_checks.get('action', 'none')

        metrics_unavailable = (
            not risk_checks['all_checks_passed']
            and risk_checks.get('reason') == 'No metrics available'
        )
        if metrics_unavailable:
            self.circuit_breaker.record_failure("risk_metrics")
            if self.circuit_breaker.is_tripped("risk_metrics"):
                logger.error("Risk metrics unavailable for too long — cancelling all orders")
                self.order_manager.cancel_all_orders()
                return
        else:
            self.circuit_breaker.record_success("risk_metrics")

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

            if action == 'cooldown':
                # Cooldown: exit without closing positions. Unlike stop_bot/force_close,
                # cooldown is triggered by leverage spikes where forced closes could
                # worsen slippage. Watchdog restarts in ~5min with fresh state;
                # existing positions are managed on restart.
                logger.warning(
                    "In emergency stop cooldown – exiting so watchdog can restart fresh"
                )
                self.running = False
                return

            if action == 'block_new_orders':
                # Block new orders but continue managing existing positions
                self.order_manager.update_order_status()
                self._check_per_trade_stops()
                return

            # pause: orders already cancelled above
            return

        self.order_manager.update_order_status()

        # Per-trade stop loss check (every cycle, if enabled)
        self._check_per_trade_stops()

        # Strategy execution with circuit breaker
        if self.circuit_breaker.is_tripped("strategy"):
            logger.warning("Strategy circuit breaker tripped — skipping signal generation")
        else:
            try:
                self.strategy.run(self.coins)
                self.circuit_breaker.record_success("strategy")
                if self.adverse_tracker:
                    self.adverse_tracker.maybe_log_summary()
                if self.imbalance_guard:
                    self.imbalance_guard.maybe_log_summary()
            except TransientError as e:
                logger.warning(f"Strategy execution hit transient error: {e}")
                self.circuit_breaker.record_failure("strategy")
            except API_ERRORS as e:
                logger.error(f"Strategy execution failed: {e}")
                self.circuit_breaker.record_failure("strategy")

        if int(time.time()) % 60 == 0:
            risk_summary = self.risk_manager.get_risk_summary()
            cb_status = self.circuit_breaker.get_status()
            logger.info(f"Risk summary: {risk_summary}")
            if cb_status:
                logger.info(f"Circuit breaker status: {cb_status}")

    # ------------------------------------------------------------------ #
    #  Position management helpers
    # ------------------------------------------------------------------ #

    def _close_position(self, pos: Dict, reason: str = "") -> bool:
        """Market-close a single position. Returns True on success."""
        coin = pos.get('coin', '')
        size = float(pos.get('szi', 0))
        return close_position_market(
            coin, size, self.market_data, self.order_manager, reason=reason,
        )

    def _close_all_positions(self) -> None:
        """Market-close every open position."""
        try:
            positions = self.order_manager.get_all_positions()
            if not positions:
                logger.info("No open positions to close")
                return
            failed = []
            for pos in positions:
                if not self._close_position(pos):
                    failed.append(pos.get('coin', 'UNKNOWN'))
            if failed:
                logger.error(f"Failed to close positions for: {', '.join(failed)}")
        except API_ERRORS as e:
            logger.error(f"Error closing all positions: {e}", exc_info=True)

    def _check_per_trade_stops(self) -> None:
        """Close individual positions that exceed the per-trade stop loss."""
        if self.risk_manager.per_trade_stop_loss is None:
            return
        try:
            positions = self.order_manager.get_all_positions()
            to_close = self.risk_manager.check_per_trade_stop_loss(positions)
            for pos in to_close:
                self._close_position(pos, reason="Per-trade stop loss")
        except API_ERRORS as e:
            logger.error(f"Error in per-trade stop loss check: {e}", exc_info=True)

    def _signal_handler(self, signum: int, frame: Optional[types.FrameType]) -> None:
        logger.info("Received shutdown signal")
        self.running = False

        if self.imbalance_guard:
            self.imbalance_guard.stop()
            self.imbalance_guard = None
        if self.adverse_tracker:
            self.adverse_tracker.stop()
            self.adverse_tracker = None
        if self.close_refresh_guard:
            self.close_refresh_guard.stop()
            self.close_refresh_guard = None
        if self.velocity_guard:
            self.velocity_guard.stop()
            self.velocity_guard = None
        if self.bbo_guard:
            self.bbo_guard.stop()
            self.bbo_guard = None
        if self.fill_feed:
            self.fill_feed.stop()
            self.fill_feed = None
        if self.ws_feed:
            self.ws_feed.stop()
            self.ws_feed = None

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
            logger.error(f"Error during shutdown (order cancellation): {e}", exc_info=True)

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

        except API_ERRORS as e:
            logger.error(f"Error during configuration validation: {e}", exc_info=True)
            logger.warning("Proceeding with caution — validation could not complete")
            return True

    def _reset_connections(self) -> None:
        """Reset connections when encountering persistent errors"""
        try:
            logger.info("Resetting connections due to persistent errors...")
            time.sleep(5)  # Give some time before reconnecting

            self._init_connections()

            # Preserve cooldown state across connection resets
            prev_emergency_stop_time = self.risk_manager._emergency_stop_time
            prev_daily_starting_balance = self.risk_manager.daily_starting_balance
            self.risk_manager = RiskManager(
                self.info, self.account_address, self.risk_config,
                hip3_dexes=self.hip3_dexes,
                market_data=self.market_data if self.hip3_dexes else None,
            )
            self.risk_manager._emergency_stop_time = prev_emergency_stop_time
            self.risk_manager.daily_starting_balance = prev_daily_starting_balance

            # Re-initialize strategy with new connections
            strategy_config = self.strategy.config if hasattr(self.strategy, 'config') else {}
            self.strategy.__init__(self.market_data, self.order_manager, strategy_config)

            logger.info("Connections reset successfully")
            self.connection_retry_count = 0

        except Exception as e:
            logger.error(f"Failed to reset connections (attempt #{self.connection_retry_count + 1}): {e}",
                         exc_info=True)
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
    parser.add_argument(
        '--enable-ws',
        action='store_true',
        default=False,
        help='Enable WebSocket feed for real-time L2 book updates (reduces REST API calls)'
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
    parser.add_argument('--config', dest='config_paths', action='append', default=None,
                        help='Path to a JSON config file. Repeat for layered configs '
                             '(later files override earlier). Also reads $BOT_CONFIG '
                             'if no --config flag is supplied. Layering precedence: '
                             'CLI > env > JSON > dataclass defaults.')

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
    parser.add_argument('--refresh-interval', dest='refresh_interval_seconds', type=float,
                        help='Seconds before cancelling stale orders (market_making, default: 30)')
    parser.add_argument('--refresh-tolerance-bp', type=float,
                        help='Keep an order across cycles when its price drifted no more than this many '
                             'basis points from the current ideal price (market_making, default: 0 = disabled). '
                             'Preserves queue priority and reduces API churn when the market is quiet.')
    parser.add_argument('--refresh-max-age-seconds', type=float,
                        help='Safety-net upper bound on the age of a kept order even when its price is still '
                             'within tolerance (market_making, default: refresh_interval_seconds * 4).')
    parser.add_argument('--no-close-immediately', action='store_true', default=False,
                        help='Disable immediate position closing (market_making)')
    parser.add_argument('--drain-flag-file', type=str,
                        help='Path to a flag file. When the file exists, the strategy enters '
                             'drain mode: stops placing new entry orders and only manages '
                             'existing positions until the file is removed. Used to gracefully '
                             'unwind before a session switch. Empty/unset = disabled. (market_making)')
    parser.add_argument('--max-position-age', dest='max_position_age_seconds', type=float,
                        help='Max seconds to hold a position before force-closing (market_making, default: 120)')
    parser.add_argument('--maker-only', action='store_true', default=False,
                        help='Use post-only (maker) orders for all trades including closes (market_making)')
    parser.add_argument('--taker-fallback-age', dest='taker_fallback_age_seconds', type=float,
                        help='Seconds after max-position-age to fall back to taker for force-close. '
                             'Not set = never use taker. 0 = taker at max-position-age. (market_making)')
    parser.add_argument('--aggressive-loss-bps', type=float,
                        help='Max loss in bps accepted to avoid taker close (default: 1.0). '
                             '0 = breakeven only, no loss-accepting tier. (market_making)')
    parser.add_argument('--force-close-max-loss-bps', type=float,
                        help='Max loss bps during force-close phase, scaling from aggressive-loss-bps '
                             '(default: 0 = disabled, use BBO-only pricing)')
    parser.add_argument('--close-spread-bps', type=float,
                        help='Close order spread in bps (default: same as --spread-bps). '
                             'Lower values increase maker close fill rate.')
    parser.add_argument('--close-breakeven-pct', type=float,
                        help='Fraction of max_position_age at which close tier transitions to breakeven '
                             '(default: 0.50, i.e. 50%% of max age)')
    parser.add_argument('--close-aggressive-pct', type=float,
                        help='Fraction of max_position_age at which close tier transitions to aggressive '
                             '(default: 0.75, i.e. 75%% of max age)')
    parser.add_argument('--bbo-mode', action='store_true',
                        help='Place orders at BBO instead of mid±spread (market_making)')
    parser.add_argument('--bbo-offset-bps', type=float,
                        help='Offset from BBO in bps (0=at BBO, 1=1bp behind). '
                             'Defaults to 0.1 when --maker-only to reduce Alo rejection risk. '
                             'Negative values are clamped to 0. (market_making)')
    parser.add_argument('--inventory-skew-bps', type=float,
                        help='Skew prices per unit of inventory to encourage position reduction. '
                             '0=disabled, 2=shift 2bps per order-size of inventory. (market_making)')
    parser.add_argument('--imbalance-threshold', type=float,
                        help='Skip quoting one side when L2 book imbalance exceeds this (0-1). '
                             '0=disabled, 0.4=skip when 70%%/30%% imbalance. (market_making)')
    parser.add_argument('--loss-streak-limit', type=int,
                        help='Cooldown a coin after this many consecutive losses. '
                             '0=disabled, 2=cooldown after 2 losses. (market_making)')
    parser.add_argument('--loss-streak-cooldown', type=float,
                        help='Seconds to pause a coin after hitting loss streak limit (default: 300, market_making)')
    parser.add_argument('--bbo-guard-threshold-bps', type=float,
                        help='Cancel orders when BBO changes by this many bps; 0 to disable (default: 2.0)')
    parser.add_argument('--imbalance-guard-threshold', type=float,
                        help='Cancel one side when L2 imbalance exceeds this (0-1); 0 to disable (default: 0)')
    parser.add_argument('--imbalance-guard-depth', type=int,
                        help='Number of L2 book levels for imbalance guard calculation (default: 5)')
    parser.add_argument('--enable-adverse-selection-log', action='store_true', default=False,
                        help='Enable post-fill adverse selection measurement logging')
    parser.add_argument('--adverse-selection-log-interval', type=float,
                        help='Adverse selection summary log interval in seconds (default: 300)')
    parser.add_argument('--rejection-log-level', type=str,
                        choices=['error', 'warning', 'info', 'debug'],
                        help='Log level for routine post-only order rejections '
                             '(default: error — preserves legacy behaviour). '
                             'Set to "warning" to reduce ERROR noise once the '
                             '5min summary line is trusted.')
    parser.add_argument('--rejection-summary-interval', type=float,
                        help='Order rejection aggregate summary interval in seconds; '
                             '0 disables the summary (default: 300)')
    parser.add_argument('--coin-offset-overrides', type=str, default='',
                        help='Per-coin BBO offset overrides in bps (e.g. "SP500:0.5,MSFT:3")')
    parser.add_argument('--coin-spread-overrides', type=str, default='',
                        help='Per-coin spread overrides in bps (e.g. "SP500:8,XYZ100:15")')
    parser.add_argument('--coin-size-overrides', type=str, default='',
                        help='Per-coin order size overrides in USD (e.g. "TSLA:150,NVDA:150")')
    parser.add_argument('--coin-unrealized-loss-overrides', type=str, default='',
                        help='Per-coin unrealized-loss early-close threshold in bps '
                             '(e.g. "INTC:25,OIL:10"). Falls back to --unrealized-loss-close-bps. '
                             'Setting an override to 0 disables the feature for that coin.')
    parser.add_argument('--close-refresh-threshold-bps', type=float,
                        help='Refresh close orders when BBO changes by this many bps; 0 to disable (default: 0)')
    parser.add_argument('--spread-schedule', type=str, default='',
                        help='Per-hour spread multiplier schedule: "HOUR:MULT,..." (e.g. "14:1.5,15:1.5,3:2.0")')
    parser.add_argument('--quiet-hours-utc', type=str, default='',
                        help='UTC hours to reduce/stop quoting, comma-separated (e.g. "17" or "17,18")')
    parser.add_argument('--quiet-hours-spread-multiplier', type=float, default=0.0,
                        help='Spread multiplier during quiet hours (0=stop quoting, >0=widen spread)')
    parser.add_argument('--microprice-skew', dest='microprice_skew_enabled',
                        action='store_true', default=False,
                        help='Enable micro-price asymmetric offset for taker fill prevention (market_making)')
    parser.add_argument('--microprice-skew-multiplier', type=float,
                        help='Micro-price skew scaling factor (default: 1.0, market_making)')
    parser.add_argument('--microprice-max-skew-bps', type=float,
                        help='Max offset adjustment from micro-price skew in bps (default: 2.0)')
    parser.add_argument('--velocity-guard', dest='velocity_guard_enabled',
                        action='store_true', default=False,
                        help='Enable BBO velocity guard — cancel orders on sustained directional BBO moves')
    parser.add_argument('--velocity-consecutive', type=int,
                        help='Number of consecutive same-direction BBO moves to trigger cancel (default: 3)')
    parser.add_argument('--velocity-min-move-bps', type=float,
                        help='Minimum cumulative BBO move in bps to trigger cancel (default: 1.0)')
    parser.add_argument('--dynamic-offset', dest='dynamic_offset_enabled',
                        action='store_true', default=False,
                        help='Enable dynamic offset auto-adjustment based on adverse selection (requires --enable-ws)')
    parser.add_argument('--dynamic-offset-sensitivity', type=float,
                        help='Offset widening per 1bps of adverse selection (default: 0.5)')
    parser.add_argument('--dynamic-offset-tighten-rate', type=float,
                        help='Offset tightening rate for favorable fills (default: 0.25)')
    parser.add_argument('--dynamic-offset-max-add', dest='dynamic_offset_max_addition', type=float,
                        help='Max offset addition in bps (default: 3.0)')
    parser.add_argument('--dynamic-offset-max-reduce', dest='dynamic_offset_max_reduction', type=float,
                        help='Max offset reduction in bps (default: 1.0)')
    parser.add_argument('--dynamic-offset-floor', type=float,
                        help='Minimum offset floor in bps (default: 0.5)')
    parser.add_argument('--dynamic-offset-min-fills', type=int,
                        help='Min fills before dynamic adjustment activates (default: 5)')
    parser.add_argument('--unrealized-loss-close-bps', type=float,
                        help='Close position early via taker when unrealized loss exceeds this bps threshold; '
                             '0 = disabled (default: 0, market_making)')
    parser.add_argument('--vol-adjust', dest='vol_adjust_enabled',
                        action='store_true', default=False,
                        help='Enable volatility-adjusted BBO offset (market_making)')
    parser.add_argument('--vol-adjust-multiplier', type=float,
                        help='Volatility multiplier for offset adjustment (default: 2.0, market_making)')
    parser.add_argument('--vol-lookback', type=int,
                        help='Number of recent mid prices for volatility calc (default: 30, market_making)')
    parser.add_argument('--vol-adjust-max-offset', type=float,
                        help='Max BBO offset in bps after vol adjustment (default: 50, market_making)')
    parser.add_argument('--dynamic-age', dest='dynamic_age_enabled',
                        action='store_true', default=False,
                        help='Enable volatility-adjusted MAX_POSITION_AGE (market_making)')
    parser.add_argument('--dynamic-age-baseline-vol', type=float,
                        help='Baseline volatility in bps for dynamic age scaling (default: 1.0, market_making)')
    parser.add_argument('--dynamic-age-min', type=float,
                        help='Minimum position age in seconds when volatility is high (default: 60, market_making)')
    parser.add_argument('--dynamic-age-max', type=float,
                        help='Maximum position age in seconds when volatility is low (default: 300, market_making)')
    parser.add_argument('--auto-exclude', dest='auto_exclude_enabled',
                        action='store_true', default=False,
                        help='Auto-pause a coin when adverse selection stays past --auto-exclude-threshold-bps '
                             'for --auto-exclude-consecutive consecutive summary windows (requires '
                             '--enable-adverse-selection-log)')
    parser.add_argument('--auto-exclude-threshold-bps', type=float,
                        help='Adverse-selection threshold (bps) for auto-exclude; values <= this trigger '
                             '(default: -3.0, market_making)')
    parser.add_argument('--auto-exclude-consecutive', type=int,
                        help='Consecutive adverse summary windows required to trigger auto-exclude '
                             '(default: 3, market_making)')
    parser.add_argument('--auto-exclude-min-fills', type=int,
                        help='Minimum fills per summary window for auto-exclude to consider it '
                             '(default: 3, market_making)')
    parser.add_argument('--auto-exclude-cooldown', type=int,
                        help='Cooldown seconds after auto-exclude triggers (default: 1800, market_making)')
    parser.add_argument('--auto-exclude-window-label', type=str,
                        help='Adverse-selection sample window for auto-exclude: 5s|30s|60s '
                             '(default: 60s, market_making)')

    # Forager: composite-score auto-exclude (orthogonal to auto_exclude)
    parser.add_argument('--forager', dest='forager_enabled',
                        action='store_true', default=False,
                        help='Auto-exclude a coin when its composite health score (activity + close '
                             'maker rate + cost) stays below --forager-threshold for '
                             '--forager-consecutive checks in a row (market_making)')
    parser.add_argument('--forager-threshold', dest='forager_score_threshold', type=float,
                        help='Composite health score threshold (0-100); below this triggers '
                             '(default: 30.0, market_making)')
    parser.add_argument('--forager-consecutive', type=int,
                        help='Consecutive sub-threshold checks required to trigger forager exclude '
                             '(default: 3, market_making)')
    parser.add_argument('--forager-cooldown', dest='forager_cooldown_seconds', type=int,
                        help='Cooldown seconds after forager triggers (default: 1800, market_making)')
    parser.add_argument('--forager-w-activity', dest='forager_weight_activity', type=float,
                        help='Composite-score weight for activity dimension (default: 0.3, market_making)')
    parser.add_argument('--forager-w-quality', dest='forager_weight_quality', type=float,
                        help='Composite-score weight for close-quality dimension (default: 0.4, market_making)')
    parser.add_argument('--forager-w-cost', dest='forager_weight_cost', type=float,
                        help='Composite-score weight for cost dimension (default: 0.3, market_making)')

    # Risk guardrail parameters
    parser.add_argument('--max-position-pct', type=float,
                        help='Max single position as %% of account (default: 0.2)')
    parser.add_argument('--max-margin-usage', type=float,
                        help='Stop new orders above this margin usage (default: 0.8)')
    parser.add_argument('--force-close-margin', type=float,
                        help='Force close ALL positions above this margin ratio (disabled by default)')
    parser.add_argument('--force-close-leverage', type=float,
                        help='Force close ALL positions above this leverage (disabled by default)')
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

    # ``_COMMON_PARAMS`` and ``_STRATEGY_PARAMS`` are defined at module
    # level (top of this file) so the JSON config layer's typo detector
    # (``validation.strategy_validator.known_market_making_keys``) can
    # introspect them without circular-import gymnastics. The local
    # references below preserve the existing call-site shape of
    # ``_collect_params``.

    def _collect_params(params, source, dest):
        """Copy non-None CLI args into *dest* dict."""
        for entry in params:
            if isinstance(entry, tuple):
                arg_name, config_key = entry
            else:
                arg_name, config_key = entry, entry
            val = getattr(source, arg_name, None)
            if val is not None:
                dest[config_key] = val

    strategy_config = {}
    _collect_params(_COMMON_PARAMS, args, strategy_config)
    _collect_params(_STRATEGY_PARAMS.get(args.strategy, []), args, strategy_config)

    # market_making: boolean flags that need special handling
    if args.strategy == 'market_making':
        if args.no_close_immediately:
            strategy_config['close_immediately'] = False
        if getattr(args, 'maker_only', False):
            strategy_config['maker_only'] = True
        if getattr(args, 'enable_adverse_selection_log', False):
            strategy_config['enable_adverse_selection_log'] = True

    # Apply CLI overrides for DEX settings
    if args.dex is not None:
        os.environ["TRADING_DEXES"] = ",".join(args.dex)
        # Reload config after env change
        Config.TRADING_DEXES = args.dex
    if args.no_hl:
        Config.ENABLE_STANDARD_HL = False

    # Apply CLI overrides for risk guardrails (CLI > env > default).
    # ``_RISK_PARAMS`` is defined at module level so the JSON layer
    # (``_apply_json_risk_overrides``) and this loop stay in sync.
    for param in _RISK_PARAMS:
        val = getattr(args, param, None)
        if val is not None:
            setattr(Config, param.upper(), val)
    if args.risk_level is not None:
        os.environ["RISK_LEVEL"] = args.risk_level

    logger.info(f"Starting bot with {args.strategy} strategy")
    logger.info(f"Standard HL coins: {args.coins if Config.ENABLE_STANDARD_HL else '(disabled)'}")
    if Config.TRADING_DEXES:
        logger.info(f"HIP-3 DEXes: {Config.TRADING_DEXES}")
    if strategy_config:
        logger.info(f"Custom parameters: {json.dumps(strategy_config, indent=2)}")

    # Load JSON config layer (opt-in). Layering precedence is enforced
    # in HyperliquidBot.__init__:
    #     CLI / env (strategy_config) > JSON > dataclass defaults.
    # Sources: --config flag (repeatable, later wins), then $BOT_CONFIG
    # (only consulted when --config is absent so CLI > env still holds).
    config_paths: List[str] = list(args.config_paths or [])
    if not config_paths:
        bot_config_env = os.environ.get('BOT_CONFIG')
        if bot_config_env:
            config_paths.append(bot_config_env)
    json_overrides: Optional[Dict] = None
    if config_paths:
        try:
            known_keys = (
                known_market_making_keys()
                if args.strategy == 'market_making' else None
            )
            json_overrides = load_json_configs(
                config_paths,
                strategy_name=args.strategy,
                known_keys=known_keys,
            )
            logger.info(
                f"[config] JSON layer loaded: {len(json_overrides)} key(s) from "
                f"{len(config_paths)} file(s)"
            )
        except ConfigError as e:
            logger.error(f"{e}")
            raise SystemExit(2)

        # Risk parameters from JSON flow to ``Config`` (separate from
        # ``strategy_config``). CLI args still beat JSON because the CLI
        # override loop further down checks ``args.{param}`` first.
        _apply_json_risk_overrides(json_overrides, args)

    bot = HyperliquidBot(
        strategy_name=args.strategy,
        coins=args.coins if Config.ENABLE_STANDARD_HL else [],
        strategy_config=strategy_config if strategy_config else None,
        json_overrides=json_overrides,
        main_loop_interval=args.main_loop_interval if args.main_loop_interval is not None else 10,
        market_order_slippage=args.market_order_slippage if args.market_order_slippage is not None else 0.01,
        enable_ws=getattr(args, 'enable_ws', False),
    )
    bot.run()
