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
from coin_utils import parse_coin  # noqa: E402
from ws import (  # noqa: E402
    MarketDataFeed, FillFeed, BboGuard, ImbalanceGuard,
    CloseRefreshGuard, BboVelocityGuard, AdverseSelectionTracker,
    FillFeatureWriter, OracleDivergenceGuard, WsReconnector,
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
        'close_tier_min_seconds',
        'coin_close_tier_overrides',
        'close_tier_toxicity_enabled',
        'close_tier_toxicity_threshold_bps',
        'close_tier_toxicity_window',
        'close_tier_toxicity_multiplier',
        'close_tier_toxicity_min_fills',
        'close_tier_toxicity_floor_pct',
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
        'coin_market_overrides',
        'phase_spread_multipliers',
        'phase_open_window_minutes',
        'phase_close_window_minutes',
        'microprice_skew_enabled',
        'microprice_skew_multiplier',
        'microprice_max_skew_bps',
        'velocity_guard_enabled',
        'velocity_consecutive',
        'velocity_min_move_bps',
        'oracle_guard_enabled',
        'oracle_divergence_threshold_bps',
        'oracle_momentum_cap_pin_bps',
        'oracle_momentum_min_step_bps',
        'oracle_momentum_consecutive',
        'oracle_stale_ttl_seconds',
        'oracle_guard_block_seconds',
        'oracle_guard_min_cancel_interval',
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
        'fill_feature_log_enabled',
        'fill_feature_log_dir',
        'fill_feature_flush_interval',
        'fill_feature_max_daily_bytes',
        'fill_feature_buffer_max',
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
        self.oracle_guard: Optional[OracleDivergenceGuard] = None
        self.adverse_tracker: Optional[AdverseSelectionTracker] = None
        self.fill_feature_writer: Optional[FillFeatureWriter] = None
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
                # Fill feature logging (observation only, ML dataset).
                # Writes per-fill order book features + markout labels to
                # a daily JSONL file. Default OFF = zero behaviour change.
                'fill_feature_log_enabled': False,
                'fill_feature_log_dir': 'tracking/fill_features',
                'fill_feature_flush_interval': 60.0,
                'fill_feature_max_daily_bytes': 50_000_000,
                'fill_feature_buffer_max': 5000,
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

        # Date-keyed event overrides (COIN_EVENT_OVERRIDES) are env-only and
        # resolved for today at Config import time. Exclusions were already
        # applied to Config.DEX_COINS; size multipliers are injected here so
        # the strategy reads them from its config instead of the environment.
        if strategy_name == 'market_making' and Config.EVENT_SIZE_MULTIPLIERS:
            config.setdefault(
                'event_size_multipliers', dict(Config.EVENT_SIZE_MULTIPLIERS))
            logger.warning(
                f"[event-override] size multipliers active today: "
                f"{Config.EVENT_SIZE_MULTIPLIERS}")
        if Config.EVENT_PAST_ENTRIES:
            logger.info(
                f"[event-override] COIN_EVENT_OVERRIDES contains "
                f"{Config.EVENT_PAST_ENTRIES} past-dated entries (inert; "
                f"safe to clean up)")

        # Validate strategy parameters early
        validation_error = validate_strategy_config(strategy_name, config)
        if validation_error:
            raise ValueError(validation_error)

        # Store strategy name and coins for validation
        self.strategy_name = strategy_name
        self.strategy_config = config

        # Build full coin list: standard HL coins + HIP-3 "dex:coin" coins
        hl_coins = coins or ['BTC', 'ETH'] if Config.ENABLE_STANDARD_HL else []
        hip3_coins, delisted = self._build_hip3_coins()

        all_coins = (hl_coins if Config.ENABLE_STANDARD_HL else []) + hip3_coins

        unique_coins, duplicates = self._deduplicate_coins(all_coins)
        if duplicates:
            logger.warning(f"Duplicate coins removed from trading list: {duplicates}")

        # The BTC/ETH fallback is for an empty configuration.  When the list is
        # empty only because every configured coin was delisted, falling back
        # would trade markets nobody asked for; leave it empty so startup
        # validation aborts instead.
        self.trading_coins = unique_coins or ([] if delisted else ['BTC', 'ETH'])

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

    def _build_hip3_coins(self) -> Tuple[List[str], List[str]]:
        """Build "dex:coin" names for every HIP-3 DEX, dropping delisted assets.

        Returns (coins, delisted).  A delisted coin has no order book, so
        keeping it would only make startup validation fail for every coin.
        """
        coins: List[str] = []
        delisted: List[str] = []
        for dex in self.hip3_dexes:
            # Use per-DEX override if configured, else use discovered coins
            explicit = dex in Config.DEX_COINS
            if explicit:
                dex_coin_names = Config.DEX_COINS[dex]
            else:
                dex_coin_names = self.registry.list_coins(dex)
            dex_delisted: List[str] = []
            for name in dex_coin_names:
                coin = f"{dex}:{name}"
                if self.registry.is_delisted(dex, name):
                    dex_delisted.append(coin)
                    if explicit:
                        logger.warning(f"[coin-skip] {coin} reason=delisted — excluded from trading")
                else:
                    coins.append(coin)
            if dex_delisted and not explicit:
                logger.info(f"Skipped {len(dex_delisted)} delisted coins discovered on '{dex}': {dex_delisted}")
            delisted.extend(dex_delisted)
        return coins, delisted

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

                # Phase 4c: Oracle divergence / momentum → stale-side cancel
                if strategy_cfg is not None:
                    og = strategy_cfg.oracle_guard
                else:
                    from strategies.mm_config import OracleGuardConfig
                    og = OracleGuardConfig(
                        enabled=bool(self.strategy_config.get('oracle_guard_enabled', False)),
                    )
                if og.enabled:
                    self.oracle_guard = OracleDivergenceGuard(
                        tracker,
                        divergence_threshold_bps=og.divergence_threshold_bps,
                        momentum_cap_pin_bps=og.momentum_cap_pin_bps,
                        momentum_min_step_bps=og.momentum_min_step_bps,
                        momentum_consecutive=og.momentum_consecutive,
                        stale_ttl_seconds=og.stale_ttl_seconds,
                        block_seconds=og.block_seconds,
                        min_cancel_interval=og.min_cancel_interval,
                    )
                    self.ws_feed.add_listener(self.oracle_guard.on_l2_update)
                    # Registering the ctx listener activates the
                    # activeAssetCtx subscriptions on the feed.
                    self.ws_feed.add_ctx_listener(self.oracle_guard.on_asset_ctx_update)
                    self.strategy._oracle_guard = self.oracle_guard
                    logger.info(
                        "[ws] OracleDivergenceGuard enabled (div=%.1fbps, cap_pin=%.0fbps, "
                        "mom=%dx%.1fbps, stale_ttl=%.0fs, block=%.0fs)",
                        og.divergence_threshold_bps, og.momentum_cap_pin_bps,
                        og.momentum_consecutive, og.momentum_min_step_bps,
                        og.stale_ttl_seconds, og.block_seconds,
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

        # Inject adverse tracker into the position closer for toxicity-linked
        # close tier acceleration (fail-safe: base behaviour when unavailable)
        if self.strategy_config.get('close_tier_toxicity_enabled', False):
            closer = getattr(self.strategy, '_closer', None)
            if self.adverse_tracker and closer is not None:
                closer.set_adverse_tracker(self.adverse_tracker)
                logger.info("[ws] Close tier toxicity linked to AdverseSelectionTracker")
            else:
                logger.warning(
                    "[ws] close_tier_toxicity enabled but AdverseSelectionTracker "
                    "unavailable (requires --enable-adverse-selection-log + WS) — inactive"
                )

        # Phase 6b: Fill feature logging (observation only, ML dataset)
        if self.strategy_config.get('fill_feature_log_enabled', False):
            if self.adverse_tracker is None:
                logger.warning(
                    "[ws] fill_feature_log_enabled requires --enable-ws and "
                    "--enable-adverse-selection-log; feature logging disabled"
                )
            else:
                log_dir = self.strategy_config.get('fill_feature_log_dir', 'tracking/fill_features')
                self.fill_feature_writer = FillFeatureWriter(
                    log_dir=log_dir,
                    flush_interval=self.strategy_config.get('fill_feature_flush_interval', 60.0),
                    max_daily_bytes=self.strategy_config.get('fill_feature_max_daily_bytes', 50_000_000),
                    buffer_max=self.strategy_config.get('fill_feature_buffer_max', 5000),
                )
                self.adverse_tracker.set_feature_writer(self.fill_feature_writer)
                logger.info(f"[ws] FillFeatureWriter enabled (dir={log_dir})")
                # Publish per-coin realized volatility into the feature record.
                # RV is a market-making strategy signal; guard for other
                # strategies that lack the hooks. This also links the tracker so
                # RV flows even when dynamic-offset is disabled.
                if hasattr(self.strategy, '_publish_vol_for_logging'):
                    self.strategy._adverse_tracker = self.adverse_tracker
                    self.strategy._publish_vol_for_logging = True
                    logger.info("[ws] Realized-vol publishing enabled for fill-feature log")

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

        # Flush buffered feature records on loop exit. The internal
        # stop_bot (daily-loss) and cooldown exits set ``self.running =
        # False`` and never reach ``_signal_handler``, so without this the
        # final maturity window of records would be silently dropped.
        # ``flush_all`` is a no-op when the signal handler already flushed
        # and cleared the writer.
        if self.fill_feature_writer:
            self.fill_feature_writer.flush_all()

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
                if self.oracle_guard:
                    self.oracle_guard.maybe_log_summary()
                if self.fill_feature_writer:
                    self.fill_feature_writer.maybe_flush()
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
        if self.fill_feature_writer:
            self.fill_feature_writer.flush_all()
            self.fill_feature_writer = None
        if self.close_refresh_guard:
            self.close_refresh_guard.stop()
            self.close_refresh_guard = None
        if self.velocity_guard:
            self.velocity_guard.stop()
            self.velocity_guard = None
        if self.oracle_guard:
            self.oracle_guard.stop()
            self.oracle_guard = None
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
            resolved = self._resolve_startup_prices()
            if resolved is None:
                return False
            current_prices, unavailable = resolved
            if unavailable:
                self._exclude_coins(unavailable)

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

    def _fetch_startup_price(self, coin: str) -> Optional[float]:
        """Mid price for *coin*, retried STARTUP_PRICE_RETRIES times before giving up."""
        attempts = 1 + Config.STARTUP_PRICE_RETRIES
        for attempt in range(attempts):
            market_data = self.market_data.get_market_data(coin)
            if market_data:
                return market_data.mid_price
            if attempt < attempts - 1:
                time.sleep(Config.STARTUP_PRICE_RETRY_INTERVAL)
        return None

    def _resolve_startup_prices(self) -> Optional[Tuple[Dict[str, float], List[str]]]:
        """Fetch a mid price for every trading coin.

        Returns (prices, unavailable) where *unavailable* lists coins to drop
        for this session, or None when startup must abort.  Coins are only
        dropped when SKIP_UNAVAILABLE_COINS is set, at least one coin remains,
        and the dropped coin is confirmed to have no open position.
        """
        if not self.trading_coins:
            logger.error("No tradable coins left after excluding delisted markets")
            return None

        prices: Dict[str, float] = {}
        unavailable: List[str] = []
        for coin in self.trading_coins:
            price = self._fetch_startup_price(coin)
            if price is None:
                unavailable.append(coin)
            else:
                prices[coin] = price

        if not unavailable:
            return prices, []

        if not Config.SKIP_UNAVAILABLE_COINS:
            logger.error(f"Could not get market price for {unavailable[0]}")
            logger.error("Unable to validate configuration without current market prices")
            logger.error("Please check your internet connection and try again")
            return None

        if len(unavailable) == len(self.trading_coins):
            logger.error(f"Could not get market price for any coin {unavailable} — "
                         f"likely a connectivity problem, not skipping")
            return None

        for coin in unavailable:
            has_position = self._has_open_position(coin)
            if has_position is None:
                logger.error(f"Could not get market price for {coin} and could not confirm "
                             f"it has no open position — refusing to skip it")
                return None
            if has_position:
                logger.error(f"Could not get market price for {coin}, which has an open position — "
                             f"refusing to skip it (the position would be left unmanaged)")
                return None

        for coin in unavailable:
            logger.error(f"[coin-skip] {coin} reason=no_price retries={Config.STARTUP_PRICE_RETRIES} — "
                         f"excluded for this session")
        return prices, unavailable

    def _has_open_position(self, coin: str) -> Optional[bool]:
        """Whether *coin* has a non-zero position; None if that cannot be confirmed.

        Queries the clearinghouse directly because the order manager's
        position helpers return an empty list on API errors, which would be
        indistinguishable from "no position".
        """
        from rate_limiter import api_wrapper
        dex, coin_name = parse_coin(coin)
        try:
            state = api_wrapper.call(self.info.user_state, self.account_address, dex=dex or "")
        except API_ERRORS as e:
            logger.error(f"Error fetching user state for {coin}: {e}")
            return None
        if not isinstance(state, dict) or 'assetPositions' not in state:
            return None
        for entry in state['assetPositions']:
            position = entry.get('position', {})
            if position.get('coin') in (coin_name, coin) and float(position.get('szi', 0) or 0) != 0:
                return True
        return False

    def _exclude_coins(self, excluded: List[str]) -> None:
        """Drop *excluded* from the trading list used by the WS feed and strategy loop."""
        dropped = set(excluded)
        self.trading_coins = [c for c in self.trading_coins if c not in dropped]
        # self.coins aliases trading_coins; rebind both so neither keeps the old list.
        self.coins = self.trading_coins
        logger.warning(f"Trading coins after startup exclusions: {self.trading_coins}")

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
    import json

    from cli import build_parser

    parser = build_parser()
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
