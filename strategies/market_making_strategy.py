"""Market-making strategy for Hyperliquid.

Places buy and sell limit orders symmetrically around the current mid
price, optionally closes filled positions immediately, and periodically
refreshes stale orders.

Order tracking and position close management are delegated to
:class:`OrderTracker` and :class:`PositionCloser` respectively.
"""

import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from strategies.base_strategy import BaseStrategy
from strategies.mm_config import (
    DYNAMIC_AGE_LOG_INTERVAL,
    FILL_RATE_LOG_INTERVAL,
    INVENTORY_SKEW_CAP,
    MMConfig,
    parse_coin_overrides,
    parse_spread_schedule,
)
from strategies.coin_health_tracker import CoinHealthTracker
from strategies.mm_order_tracker import OrderTracker
from strategies.mm_position_closer import PositionCloser
from coin_utils import parse_coin
from order_manager import BBO_OFFSET, OrderSide, round_price
from order_rejection_tracker import (
    ALLOWED_LOG_LEVELS,
    OrderRejectionTracker,
)
from rate_limiter import API_ERRORS

logger = logging.getLogger(__name__)

# Minimum offset (in fraction) from BBO to avoid post-only rejections


class MarketMakingStrategy(BaseStrategy):

    def __init__(self, market_data_manager, order_manager, config: Dict) -> None:
        super().__init__(market_data_manager, order_manager, config)

        # ---- Phase 1 grouped config (LossStreak / Microprice / Velocity / PerCoin) ---- #
        # The flat ``self.X`` attributes below remain as aliases for backward
        # compatibility with tests and call-sites that bypass __init__ (e.g.
        # ``MarketMakingStrategy.__new__(cls)``). Future refactor phases will
        # migrate internal references to ``self.cfg.<group>.<field>`` directly.
        self.cfg: MMConfig = MMConfig.from_legacy_dict(config)

        # ---- Configurable parameters ---- #
        self.spread_bps: float = config.get('spread_bps', 5)
        self.order_size_usd: float = config.get('order_size_usd', 50)
        self.max_open_orders: int = config.get('max_open_orders', 4)
        self.close_immediately: bool = config.get('close_immediately', True)
        self.max_positions: int = config.get('max_positions', 3)
        self.maker_only: bool = config.get('maker_only', False)
        self.account_cap_pct: float = config.get('account_cap_pct', 0.05)
        self.bbo_mode: bool = config.get('bbo_mode', False)
        raw_offset = config.get('bbo_offset_bps', None)
        if raw_offset is not None:
            self.bbo_offset_bps = max(0.0, float(raw_offset))
        elif self.bbo_mode and self.maker_only:
            # Default to small offset to reduce Alo rejection risk in fast markets
            self.bbo_offset_bps = 0.1
        else:
            self.bbo_offset_bps = 0.0
        self.inventory_skew_bps: float = config.get('inventory_skew_bps', 0)
        self.inventory_skew_cap: float = config.get('inventory_skew_cap', INVENTORY_SKEW_CAP)

        # ---- L2 book imbalance (alias of self.cfg.imbalance.placement_threshold) ---- #
        self.imbalance_threshold: float = self.cfg.imbalance.placement_threshold

        # ---- Per-coin loss streak cooldown (alias of self.cfg.loss_streak) ---- #
        self.loss_streak_limit: int = self.cfg.loss_streak.limit
        self.loss_streak_cooldown: float = self.cfg.loss_streak.cooldown_seconds
        self._loss_streaks: Dict[str, int] = defaultdict(int)  # coin -> consecutive losses
        self._coin_cooldown_until: Dict[str, float] = {}  # coin -> monotonic deadline

        # ---- Quiet hours (aliases of self.cfg.schedule) ---- #
        self._quiet_hours: Set[int] = self.cfg.schedule.quiet_hours_utc
        self._quiet_spread_multiplier: float = self.cfg.schedule.quiet_hours_spread_multiplier
        self._was_quiet: bool = False
        if self._quiet_hours:
            mode = f"spread×{self._quiet_spread_multiplier:.1f}" if self._quiet_spread_multiplier > 0 else "stop"
            logger.info(f"[mm] Quiet hours enabled: UTC {sorted(self._quiet_hours)} mode={mode}")

        # ---- Drain mode (graceful pre-shutdown) ---- #
        # Path to a flag file. When the file exists, the strategy stops
        # placing new entry orders and only manages existing positions
        # (maker-first close). Designed to be triggered by an external
        # process before SIGTERM to reduce taker-close losses at session
        # boundaries. Empty/None = feature disabled.
        self._drain_flag_file: str = str(config.get('drain_flag_file', '') or '')
        self._was_drain: bool = False
        if self._drain_flag_file:
            logger.info(f"[mm] Drain mode armed: flag_file={self._drain_flag_file}")

        # ---- Hourly spread schedule (alias of self.cfg.schedule.spread_schedule) ---- #
        self._spread_schedule: Dict[int, float] = self.cfg.schedule.spread_schedule
        if self._spread_schedule:
            logger.info(f"[mm] Spread schedule: {dict(sorted(self._spread_schedule.items()))}")

        # ---- Dynamic offset auto-adjustment (aliases of self.cfg.dynamic_offset) ---- #
        self._dynamic_offset_enabled: bool = self.cfg.dynamic_offset.enabled
        self._dynamic_offset_sensitivity: float = self.cfg.dynamic_offset.sensitivity
        self._dynamic_offset_tighten_rate: float = self.cfg.dynamic_offset.tighten_rate
        self._dynamic_offset_max_addition: float = self.cfg.dynamic_offset.max_addition
        self._dynamic_offset_max_reduction: float = self.cfg.dynamic_offset.max_reduction
        self._dynamic_offset_floor: float = self.cfg.dynamic_offset.floor
        self._dynamic_offset_min_fills: int = self.cfg.dynamic_offset.min_fills
        self._adverse_tracker = None  # set by bot.py after WS init
        if self._dynamic_offset_enabled:
            logger.info(
                f"[mm] Dynamic offset enabled: sensitivity={self._dynamic_offset_sensitivity}, "
                f"floor={self._dynamic_offset_floor}, max_add={self._dynamic_offset_max_addition}"
            )

        # ---- Auto-exclude on consecutive adverse-selection windows ---- #
        if self.cfg.auto_exclude.enabled:
            logger.info(
                f"[mm] Auto-exclude armed: threshold={self.cfg.auto_exclude.threshold_bps}bps, "
                f"consecutive={self.cfg.auto_exclude.consecutive}, "
                f"min_fills={self.cfg.auto_exclude.min_fills}, "
                f"window={self.cfg.auto_exclude.window_label}, "
                f"cooldown={self.cfg.auto_exclude.cooldown_seconds}s"
            )

        # ---- Per-coin entry-side position cap (alias of self.cfg.position_cap) ---- #
        # When accumulated |position| * mid >= max_position_multiple * order_size_usd,
        # suppress same-direction entries to prevent oversized force-close events.
        # 0.0 = disabled (legacy behaviour).
        self._max_position_multiple: float = self.cfg.position_cap.max_position_multiple
        if self._max_position_multiple > 0:
            logger.info(
                f"[mm] Position cap armed: max_position_multiple="
                f"{self._max_position_multiple}x order_size_usd"
            )

        # ---- Forager: composite per-coin health scoring ---- #
        self._coin_health_tracker: Optional[CoinHealthTracker] = (
            CoinHealthTracker(self.cfg.forager) if self.cfg.forager.enabled else None
        )
        # Per-coin sliding history of recent composite scores (size = consecutive)
        self._forager_score_history: Dict[str, deque] = defaultdict(deque)
        # Per-coin throttle for `_check_forager_health` (avoid evaluating every loop)
        self._forager_last_check: Dict[str, float] = {}
        if self.cfg.forager.enabled:
            logger.info(
                f"[mm] Forager armed: threshold={self.cfg.forager.score_threshold}, "
                f"consecutive={self.cfg.forager.consecutive}, "
                f"weights=(act={self.cfg.forager.weight_activity}, "
                f"qual={self.cfg.forager.weight_quality}, "
                f"cost={self.cfg.forager.weight_cost}), "
                f"window={self.cfg.forager.window_seconds}s, "
                f"cooldown={self.cfg.forager.cooldown_seconds}s"
            )

        # ---- Order rejection tracker (log downgrade + 5min summary) ---- #
        # Routes routine post-only rejections through a classifier so the
        # log level can be tuned per deployment and per-coin counts get
        # aggregated into a single ``[reject-summary]`` line every
        # ``rejection_summary_interval`` seconds. With the default
        # log_level=error this preserves the legacy behaviour exactly,
        # while the summary itself is purely additive.
        rejection_log_level: str = str(
            config.get('rejection_log_level', 'error') or 'error'
        ).lower()
        if rejection_log_level not in ALLOWED_LOG_LEVELS:
            logger.warning(
                f"[mm] Unknown rejection_log_level={rejection_log_level!r}, "
                f"falling back to 'error'"
            )
            rejection_log_level = 'error'
        rejection_summary_interval: float = float(
            config.get('rejection_summary_interval', 300.0)
        )
        self._rejection_tracker: OrderRejectionTracker = OrderRejectionTracker(
            routine_log_level=rejection_log_level,
            summary_interval=rejection_summary_interval,
        )
        self.order_manager.set_rejection_tracker(self._rejection_tracker)
        if rejection_log_level != 'error' or rejection_summary_interval > 0:
            logger.info(
                f"[mm] Rejection tracker armed: log_level={rejection_log_level}, "
                f"summary_interval={rejection_summary_interval}s"
            )

        # ---- Per-coin offset/spread/size overrides (aliases of self.cfg.per_coin) ---- #
        self._coin_offset_overrides: Dict[str, float] = self.cfg.per_coin.offset
        self._coin_spread_overrides: Dict[str, float] = self.cfg.per_coin.spread
        self._coin_size_overrides: Dict[str, float] = self.cfg.per_coin.size
        self._coin_unrealized_loss_overrides: Dict[str, float] = self.cfg.per_coin.unrealized_loss
        if self._coin_offset_overrides:
            logger.info(f"[mm] Per-coin offset overrides: {self._coin_offset_overrides}")
        if self._coin_spread_overrides:
            logger.info(f"[mm] Per-coin spread overrides: {self._coin_spread_overrides}")
        if self._coin_size_overrides:
            logger.info(f"[mm] Per-coin size overrides: {self._coin_size_overrides}")
        if self._coin_unrealized_loss_overrides:
            logger.info(
                f"[mm] Per-coin unrealized-loss overrides: {self._coin_unrealized_loss_overrides}"
            )

        # ---- Micro-price asymmetric offset (aliases of self.cfg.microprice) ---- #
        self._microprice_enabled: bool = self.cfg.microprice.enabled
        self._microprice_multiplier: float = self.cfg.microprice.multiplier
        self._microprice_max_skew_bps: float = self.cfg.microprice.max_skew_bps
        if self._microprice_enabled:
            logger.info(
                f"[mm] Micro-price skew enabled: multiplier={self._microprice_multiplier}, "
                f"max_skew={self._microprice_max_skew_bps}bps"
            )

        # ---- Volatility-adjusted BBO offset ---- #
        self.vol_adjust_enabled: bool = config.get('vol_adjust_enabled', False)
        self.vol_adjust_multiplier: float = config.get('vol_adjust_multiplier', 2.0)
        self.vol_lookback: int = config.get('vol_lookback', 30)
        self.vol_adjust_max_offset: float = config.get('vol_adjust_max_offset', 50.0)
        self._recent_mids: Dict[str, deque] = {}  # coin -> deque of recent mid prices

        # ---- Dynamic position age (aliases of self.cfg.dynamic_age) ---- #
        self._dynamic_age_enabled: bool = self.cfg.dynamic_age.enabled
        self._dynamic_age_baseline_vol: float = self.cfg.dynamic_age.baseline_vol_bps
        self._dynamic_age_min: float = self.cfg.dynamic_age.min_seconds
        self._dynamic_age_max: float = self.cfg.dynamic_age.max_seconds
        self._base_max_position_age: float = config.get('max_position_age_seconds', 120.0)
        # coin -> (avg_move_bps, computed_age_seconds) for periodic logging
        self._dynamic_age_recent: Dict[str, Tuple[float, float]] = {}
        # coin -> {min_clamp, max_clamp, mid, raw_sum, raw_min, raw_max, samples}
        # Populated by _get_dynamic_position_age and reset on each summary log
        # so per-coin clamp distribution can be observed over the log interval.
        self._dynamic_age_clamp_stats: Dict[str, Dict[str, float]] = {}
        self._dynamic_age_log_interval: float = config.get(
            'dynamic_age_log_interval', DYNAMIC_AGE_LOG_INTERVAL
        )
        self._last_dynamic_age_log: float = 0.0
        if self._dynamic_age_enabled:
            logger.info(
                f"[mm] Dynamic position age enabled: baseline_vol={self._dynamic_age_baseline_vol}bps, "
                f"min={self._dynamic_age_min}s, max={self._dynamic_age_max}s"
            )

        # ---- Fill rate tracking ---- #
        self._orders_placed: int = 0
        self._fills_detected: int = 0
        self._orders_placed_per_coin: Dict[str, int] = defaultdict(int)
        self._fills_per_coin: Dict[str, int] = defaultdict(int)
        self._fill_rate_log_interval: float = config.get(
            'fill_rate_log_interval', FILL_RATE_LOG_INTERVAL
        )
        self._last_fill_rate_log: float = 0.0
        self._prev_position_coins: set = set()  # coins that had positions last cycle
        self._prev_positions: Dict[str, Dict] = {}  # snapshot for loss streak detection

        # ---- Refresh tolerance (preserve queue priority on small price drift) ---- #
        # When ``refresh_tolerance_bp > 0``, an order is kept across cycles
        # as long as both (a) its recorded price drifted no more than
        # ``refresh_tolerance_bp`` basis points from the current ideal
        # price, and (b) its age is below ``refresh_max_age_seconds``.
        # ``refresh_tolerance_bp == 0`` (default) preserves the original
        # age-only behaviour (full backward compatibility).
        self.refresh_tolerance_bp: float = max(
            0.0, float(config.get('refresh_tolerance_bp', 0))
        )
        refresh_interval = float(config.get('refresh_interval_seconds', 30))
        raw_max_age = config.get('refresh_max_age_seconds', None)
        if raw_max_age is None:
            self.refresh_max_age_seconds: float = max(refresh_interval * 4.0, refresh_interval)
        else:
            self.refresh_max_age_seconds = max(float(raw_max_age), refresh_interval)
        if getattr(self, 'refresh_tolerance_bp', 0) > 0:
            logger.info(
                f"[mm] Refresh tolerance enabled: tolerance={self.refresh_tolerance_bp}bp, "
                f"max_age={self.refresh_max_age_seconds}s"
            )

        # ---- Delegates ---- #
        self._tracker = OrderTracker(
            order_manager=order_manager,
            refresh_interval_seconds=refresh_interval,
            max_open_orders=self.max_open_orders,
        )
        self._closer = PositionCloser(
            order_manager=order_manager,
            market_data=market_data_manager,
            spread_bps=self.spread_bps,
            max_position_age_seconds=config.get('max_position_age_seconds', 120),
            maker_only=self.maker_only,
            taker_fallback_age_seconds=config.get('taker_fallback_age_seconds', None),
            aggressive_loss_bps=config.get('aggressive_loss_bps', 1.0),
            force_close_max_loss_bps=self.cfg.close.force_close_max_loss_bps,
            coin_spread_overrides=self._coin_spread_overrides,
            close_spread_bps=self.cfg.close.spread_bps,
            close_breakeven_pct=self.cfg.close.breakeven_pct,
            close_aggressive_pct=self.cfg.close.aggressive_pct,
            unrealized_loss_close_bps=self.cfg.close.unrealized_loss_close_bps,
            coin_unrealized_loss_overrides=self._coin_unrealized_loss_overrides,
        )

    @property
    def order_tracker(self) -> OrderTracker:
        """Public accessor for the order tracker (used by WS FillFeed)."""
        return self._tracker

    # ------------------------------------------------------------------ #
    #  Main loop override
    # ------------------------------------------------------------------ #

    def run(self, coins: List[str]) -> None:
        """Override the default signal-based loop with a market-making loop.

        Flow per coin:
        1. If position exists and close_immediately: market-close it.
        2. If position exists and not close_immediately: manage take-profit.
        3. Cancel stale orders.
        4. Place new buy + sell limit orders if no position and capacity.
        """
        self.update_positions()

        # Detect new fills: a position appearing for a coin that had no
        # position last cycle means at least one maker order was filled.
        # NOTE: This is a coarse approximation. It undercounts when multiple
        # fills occur within a single cycle or when close_immediately=True
        # (positions are closed before the next detection pass). Sufficient
        # for spread-tuning observability; not intended as exact accounting.
        current_position_coins = set()
        for coin in coins:
            if coin in self.positions and abs(self.positions[coin].get('size', 0)) > 0:
                current_position_coins.add(coin)
        new_fills = current_position_coins - self._prev_position_coins
        for coin in new_fills:
            self._fills_detected += 1
            self._fills_per_coin[coin] += 1
            # Forager: update activity dimension on entry fills.
            tracker = getattr(self, '_coin_health_tracker', None)
            if tracker is not None:
                tracker.record_fill(coin)
            # Cancel opposite-side orders for newly filled coins to prevent
            # double-filling which doubles adverse selection cost.
            # NOTE: Does not fire when close_immediately=True because
            # positions are closed before the next detection pass.
            self._tracker.cancel_all_orders_for_coin(coin, reason="fill")
            logger.info(f"[mm] Cancelled orders for {coin} after fill (prevent double-fill)")

        # Track loss streaks: detect coins that just closed (had position last cycle, not now)
        if self.loss_streak_limit > 0:
            just_closed = self._prev_position_coins - current_position_coins
            for coin in just_closed:
                last_pos = self._prev_positions.get(coin, {})
                entry_px = float(last_pos.get('entryPx', 0))
                size = float(last_pos.get('size', 0))
                # Estimate close PnL from entry price and current mid price.
                # This is an approximation — actual close price may differ
                # slightly, but the sign (win/loss) is reliable enough for
                # streak detection since closes happen near current price.
                md = self.market_data.get_market_data(coin)
                if md and entry_px > 0 and size != 0:
                    estimated_pnl = (md.mid_price - entry_px) * size
                else:
                    estimated_pnl = float(last_pos.get('unrealizedPnl', 0))

                if estimated_pnl < 0:
                    self._loss_streaks[coin] += 1
                    streak = self._loss_streaks[coin]
                    if streak >= self.loss_streak_limit:
                        deadline = time.monotonic() + self.loss_streak_cooldown
                        self._coin_cooldown_until[coin] = deadline
                        logger.info(
                            f"[mm] {coin} hit {streak} consecutive losses, "
                            f"cooldown {self.loss_streak_cooldown}s"
                        )
                else:
                    self._loss_streaks[coin] = 0

        self._prev_positions = {c: dict(self.positions[c]) for c in self.positions if self.positions.get(c)}
        self._prev_position_coins = current_position_coins

        self._log_fill_rate()
        self._log_dynamic_age()
        self._closer.log_close_stats()
        self._rejection_tracker.log_summary_if_due()

        # ---- Drain mode: pre-shutdown graceful close ---- #
        # Drain takes precedence over quiet hours: when an external
        # process signals an imminent shutdown via the flag file, stop
        # placing new entry orders and let existing positions close via
        # the normal maker-first PositionCloser flow. The session-switch
        # script then performs its own IOC fallback after the drain
        # window expires.
        if self._is_drain_mode():
            if not self._was_drain:
                logger.info("[mm] Entering drain mode, cancelling all orders")
                for coin in coins:
                    self._tracker.cancel_all_orders_for_coin(coin, reason="drain")
                self._was_drain = True
            for coin in coins:
                try:
                    has_position = coin in self.positions and abs(self.positions[coin]['size']) > 0
                    if has_position:
                        if self.close_immediately:
                            self.close_position(coin)
                            self._closer.on_position_closed(coin)
                        else:
                            dynamic_age = self._get_dynamic_position_age(coin)
                            self._closer.manage(coin, self.positions[coin], self.close_position,
                                                max_age_override=dynamic_age)
                    else:
                        self._closer.cleanup_closed(coin)
                except API_ERRORS as e:
                    logger.error(f"[mm] Error processing {coin} during drain: {e}")
            self._log_cycle(coins, " [DRAIN]")
            return

        if self._was_drain and not self._is_drain_mode():
            logger.info("[mm] Exiting drain mode, resuming quotes")
            self._was_drain = False

        # ---- Quiet hours: full-stop mode ---- #
        is_quiet = self._is_quiet_hour()
        if is_quiet and self._quiet_spread_multiplier <= 0:
            if not self._was_quiet:
                utc_hour = datetime.now(timezone.utc).hour
                logger.info(f"[mm] Entering quiet hours (UTC {utc_hour}), cancelling all orders")
                for coin in coins:
                    self._tracker.cancel_all_orders_for_coin(coin, reason="quiet_hour")
                self._was_quiet = True
            # Still process existing positions but skip new order placement
            for coin in coins:
                try:
                    has_position = coin in self.positions and abs(self.positions[coin]['size']) > 0
                    if has_position:
                        if self.close_immediately:
                            self.close_position(coin)
                            self._closer.on_position_closed(coin)
                        else:
                            dynamic_age = self._get_dynamic_position_age(coin)
                            self._closer.manage(coin, self.positions[coin], self.close_position,
                                                max_age_override=dynamic_age)
                    else:
                        self._closer.cleanup_closed(coin)
                except API_ERRORS as e:
                    logger.error(f"[mm] Error processing {coin} during quiet hours: {e}")
            self._log_cycle(coins, " [QUIET]")
            return

        if self._was_quiet and not is_quiet:
            utc_hour = datetime.now(timezone.utc).hour
            logger.info(f"[mm] Exiting quiet hours (UTC {utc_hour}), resuming quotes")
            self._was_quiet = False

        # Track hourly spread multiplier for cycle log suffix
        _hourly_mult = self._get_hourly_spread_multiplier()

        for coin in coins:
            try:
                has_position = coin in self.positions and abs(self.positions[coin]['size']) > 0

                if has_position:
                    if self.close_immediately:
                        logger.info(
                            f"[mm] Closing position for {coin}: "
                            f"size={self.positions[coin]['size']:.6f}"
                        )
                        self.close_position(coin)
                        self._closer.on_position_closed(coin)
                        continue
                    else:
                        dynamic_age = self._get_dynamic_position_age(coin)
                        self._closer.manage(coin, self.positions[coin], self.close_position,
                                            max_age_override=dynamic_age)
                        continue
                else:
                    # Position was closed — clean up tracking
                    self._closer.cleanup_closed(coin)

                # No position — normal MM flow
                close_oid = self._closer.get_close_oid(coin)

                # Compute ideal prices once per cycle and reuse for both the
                # tolerance refresh decision and order placement. This also
                # ensures the volatility rolling buffer is updated exactly
                # once per cycle (inside ``_compute_ideal_prices``), so the
                # tolerance check and the placed order are evaluated against
                # the same buffer state.
                ideal = self._compute_ideal_prices(coin)

                if getattr(self, 'refresh_tolerance_bp', 0) > 0:
                    if ideal is None:
                        # Fall back to age-only when ideal price is unavailable
                        self._tracker.cancel_stale_orders(coin, close_oid=close_oid)
                    else:
                        ideal_buy, ideal_sell, _ = ideal
                        self._tracker.refresh_orders_with_tolerance(
                            coin,
                            ideal_prices={
                                OrderSide.BUY.value: ideal_buy,
                                OrderSide.SELL.value: ideal_sell,
                            },
                            tolerance_bp=self.refresh_tolerance_bp,
                            max_age_seconds=self.refresh_max_age_seconds,
                            close_oid=close_oid,
                        )
                else:
                    self._tracker.cancel_stale_orders(coin, close_oid=close_oid)

                # Check max positions using active coin count
                active_count = self._tracker.active_coins(
                    self.positions, self._closer.tracked_coins,
                )
                if active_count >= self.max_positions:
                    logger.debug(
                        f"[mm] Max active coins ({active_count}/{self.max_positions}), skipping {coin}"
                    )
                    continue

                # Auto-exclude: may set _coin_cooldown_until[coin] when adverse
                # selection has been moderate for ``consecutive`` summary
                # windows in a row.
                self._check_auto_exclude(coin)
                # Forager: composite health score (activity + maker rate + cost)
                # Independent of auto-exclude; either may set the cooldown.
                self._check_forager_health(coin)

                # Per-coin cooldown (shared by loss_streak and auto_exclude)
                cooldown_deadline = self._coin_cooldown_until.get(coin)
                now = time.monotonic()
                if cooldown_deadline and now < cooldown_deadline:
                    remaining = cooldown_deadline - now
                    logger.debug(f"[mm] {coin} in cooldown ({remaining:.0f}s left)")
                    continue
                elif cooldown_deadline:
                    # Cooldown expired — reset
                    del self._coin_cooldown_until[coin]
                    if self.loss_streak_limit > 0:
                        self._loss_streaks[coin] = 0
                    logger.info(f"[mm] {coin} cooldown expired, resuming")

                if self._tracker.get_order_count(coin) < self.max_open_orders:
                    self._place_orders(coin, ideal_prices=ideal)

            except API_ERRORS as e:
                logger.error(f"[mm] Error processing {coin}: {e}")

        # Per-cycle log with inventory skew info.
        # Format differs from base strategy's [cycle] (no signals_generated)
        # because MM uses a position-based flow, not signal-based.
        coin_statuses = []
        active_positions = 0
        for coin in coins:
            pos = self.positions.get(coin)
            if pos and pos['size'] != 0:
                active_positions += 1
                md = self.market_data.get_market_data(coin)
                mid = md.mid_price if md else 0
                skew = self._calculate_inventory_skew(coin, mid)
                if abs(skew) > 0:
                    coin_statuses.append(f"{coin}:skew{skew:+.1f}bp")
                else:
                    coin_statuses.append(f"{coin}:pos")
            else:
                # Show vol-adjusted offset when it differs from base (read-only)
                if self.vol_adjust_enabled and self.bbo_mode:
                    adj = self._get_volatility_adjusted_offset(coin)
                    if abs(adj - self.bbo_offset_bps) > 0.01:
                        coin_statuses.append(f"{coin}:idle(off={adj:.1f}bp)")
                        continue
                coin_statuses.append(f"{coin}:idle")

        max_display = getattr(self, '_max_coin_status_display', 10)
        if len(coin_statuses) <= max_display:
            status_str = " | ".join(coin_statuses)
        else:
            shown = coin_statuses[:max_display]
            status_str = " | ".join(shown) + f" ... +{len(coin_statuses) - max_display} more"
        suffix = f" [SPREAD×{_hourly_mult:.1f}]" if _hourly_mult != 1.0 else ""
        logger.info(
            f"[cycle] {len(coins)} coins, {active_positions} pos | {status_str}{suffix}"
        )

    # ------------------------------------------------------------------ #
    #  Stub implementations for abstract methods (not used in run())
    # ------------------------------------------------------------------ #

    def generate_signals(self, coin: str) -> Optional[Dict]:
        """Not used -- the market-making loop is driven by ``run()``."""
        return None

    def calculate_position_size(self, coin: str, signal: Dict) -> float:
        """Calculate order size in coin units, respecting the risk-level multiplier."""
        market_data = self.market_data.get_market_data(coin)
        if not market_data or market_data.mid_price <= 0:
            return 0.0

        base_size_usd = self._get_coin_size(coin)

        # Apply risk-level multiplier (green=100%, yellow=50%, red/black=0%)
        multiplier = self._get_risk_multiplier()
        base_size_usd *= multiplier
        if base_size_usd <= 0:
            return 0.0

        return self._apply_account_cap(base_size_usd, market_data.mid_price, cap_pct=self.account_cap_pct)

    def close_position(self, coin: str) -> None:
        """Override to add _open_positions guard before reduce_only close.

        FillFeed updates ``_open_positions`` on the WS thread with zero cache
        delay, making it more reliable than the 2-second TTL position cache
        used by the base class fresh check.
        """
        if coin not in self._closer._open_positions:
            logger.info(f"[mm] Position {coin} already closed (WS fill) before close_position")
            self.positions.pop(coin, None)
            return
        super().close_position(coin)

    # ------------------------------------------------------------------ #
    #  Order placement
    # ------------------------------------------------------------------ #

    def _record_mid_price(self, coin: str, mid_price: float) -> None:
        """Record a mid price for volatility tracking. Call once per cycle."""
        if not self.vol_adjust_enabled and not getattr(self, '_dynamic_age_enabled', False):
            return
        if coin not in self._recent_mids:
            self._recent_mids[coin] = deque(maxlen=self.vol_lookback)
        self._recent_mids[coin].append(mid_price)

    def _compute_realized_volatility(self, coin: str) -> Optional[float]:
        """Average absolute mid-price return (bps) over the recent window.

        Returns None when fewer than 5 mids are recorded for *coin* — callers
        should fall back to their default behaviour in that case.
        """
        mids = self._recent_mids.get(coin)
        if not mids or len(mids) < 5:
            return None
        returns_bps = [
            abs(mids[i] - mids[i - 1]) / mids[i - 1] * 10_000
            for i in range(1, len(mids))
        ]
        return sum(returns_bps) / len(returns_bps)

    def _get_volatility_adjusted_offset(self, coin: str, base_offset: Optional[float] = None) -> float:
        """Return BBO offset adjusted for recent volatility.

        Read-only — does not mutate state. Call _record_mid_price()
        separately to update the price history.

        Parameters
        ----------
        base_offset : float, optional
            Base offset to adjust from. If None, uses the global bbo_offset_bps.
        """
        if base_offset is None:
            base_offset = self.bbo_offset_bps

        if not self.vol_adjust_enabled or not self.bbo_mode:
            return base_offset

        avg_move_bps = self._compute_realized_volatility(coin)
        if avg_move_bps is None:
            return base_offset

        # Scale offset with cap to prevent extreme values during flash crashes
        adjusted = base_offset + self.vol_adjust_multiplier * avg_move_bps
        return min(adjusted, self.vol_adjust_max_offset)

    def _get_dynamic_position_age(self, coin: str) -> Optional[float]:
        """Calculate volatility-adjusted MAX_POSITION_AGE for a coin.

        Returns None if disabled or insufficient data (use default).
        """
        if not getattr(self, '_dynamic_age_enabled', False):
            return None

        avg_move_bps = self._compute_realized_volatility(coin)
        if avg_move_bps is None:
            return None

        # Scale: high vol -> short age, low vol -> long age
        # baseline_vol (bps) = typical move per cycle (calibrated)
        ratio = self._dynamic_age_baseline_vol / max(avg_move_bps, self._dynamic_age_baseline_vol * 0.1)
        raw_age = self._base_max_position_age * ratio

        # Clamp to [min_age, max_age]
        age = max(self._dynamic_age_min, min(raw_age, self._dynamic_age_max))

        # Record latest computation for periodic summary log
        self._dynamic_age_recent[coin] = (avg_move_bps, age)

        # Aggregate clamp distribution per coin so the periodic summary can
        # show "min_clamp=85% mid=15%" — directly tells operators whether
        # DYNAMIC_AGE_MIN is biting or the value is moving freely.
        stats = self._dynamic_age_clamp_stats.setdefault(
            coin,
            {
                "min_clamp": 0,
                "max_clamp": 0,
                "mid": 0,
                "raw_sum": 0.0,
                "raw_min": float("inf"),
                "raw_max": 0.0,
                "samples": 0,
            },
        )
        if raw_age <= self._dynamic_age_min:
            stats["min_clamp"] += 1
        elif raw_age >= self._dynamic_age_max:
            stats["max_clamp"] += 1
        else:
            stats["mid"] += 1
        stats["raw_sum"] += raw_age
        if raw_age < stats["raw_min"]:
            stats["raw_min"] = raw_age
        if raw_age > stats["raw_max"]:
            stats["raw_max"] = raw_age
        stats["samples"] += 1

        return age

    def _compute_ideal_prices(self, coin: str) -> Optional[Tuple[float, float, float]]:
        """Compute current ideal ``(buy_price, sell_price, skew_bps)`` for ``coin``.

        Mirrors the price computation that was previously inlined in
        :meth:`_place_orders`. Updates the volatility rolling buffer via
        :meth:`_record_mid_price` so callers can rely on this method as the
        single source of truth for both order placement and refresh-tolerance
        drift evaluation. Returns ``None`` when the ideal price cannot be
        determined (no market data, mid <= 0).

        The ``skew_bps`` value is returned alongside the prices so callers can
        log it without recomputing :meth:`_calculate_inventory_skew`.
        """
        market_data = self.market_data.get_market_data(coin)
        if not market_data:
            return None

        mid_price = market_data.mid_price
        if mid_price <= 0:
            return None

        rp = self.market_data.price_rounding_params(coin)

        if self.bbo_mode and market_data.bid > 0 and market_data.ask > 0:
            # Update the rolling vol buffer here so the volatility-adjusted
            # offset below sees the freshest sample in both call paths
            # (run-loop tolerance check and order placement).
            self._record_mid_price(coin, mid_price)
            base_offset = self._get_coin_offset(coin)
            if self.vol_adjust_enabled:
                effective_offset_bps = self._get_volatility_adjusted_offset(coin, base_offset)
            else:
                effective_offset_bps = base_offset
            if self._quiet_spread_multiplier > 0 and self._is_quiet_hour():
                effective_offset_bps *= self._quiet_spread_multiplier
            hourly_mult = self._get_hourly_spread_multiplier()
            if hourly_mult != 1.0:
                effective_offset_bps *= hourly_mult
            buy_offset_bps, sell_offset_bps = self._calculate_microprice_offsets(
                coin, effective_offset_bps
            )
            buy_price = round_price(market_data.bid * (1 - buy_offset_bps / 10_000), *rp)
            sell_price = round_price(market_data.ask * (1 + sell_offset_bps / 10_000), *rp)
        else:
            coin_spread = self._get_coin_spread(coin)
            spread_offset = mid_price * (coin_spread / 10_000)
            raw_buy = mid_price - spread_offset
            raw_sell = mid_price + spread_offset
            if self._quiet_spread_multiplier > 0 and self._is_quiet_hour():
                extra = spread_offset * (self._quiet_spread_multiplier - 1)
                raw_buy -= extra
                raw_sell += extra
            hourly_mult = self._get_hourly_spread_multiplier()
            if hourly_mult != 1.0 and hourly_mult > 0:
                extra = spread_offset * (hourly_mult - 1)
                raw_buy -= extra
                raw_sell += extra
            buy_price = round_price(raw_buy, *rp)
            sell_price = round_price(raw_sell, *rp)
            if self.maker_only and market_data.bid > 0 and market_data.ask > 0:
                if buy_price >= market_data.bid:
                    buy_price = round_price(market_data.bid * (1 - BBO_OFFSET), *rp)
                if sell_price <= market_data.ask:
                    sell_price = round_price(market_data.ask * (1 + BBO_OFFSET), *rp)

        # Inventory skew (same shift applied to both legs)
        skew = self._calculate_inventory_skew(coin, mid_price)
        if skew != 0.0:
            skew_mult = skew / 10_000
            buy_price = round_price(buy_price * (1 - skew_mult), *rp)
            sell_price = round_price(sell_price * (1 - skew_mult), *rp)

        return buy_price, sell_price, skew

    def _place_orders(
        self,
        coin: str,
        ideal_prices: Optional[Tuple[float, float, float]] = None,
    ) -> None:
        """Place a buy and a sell limit order.

        In BBO mode, orders are placed at or near the best bid/ask.
        Otherwise, orders are placed symmetrically around mid price at
        ``spread_bps``.  Uses ``bulk_place_orders`` for a single API call.

        ``ideal_prices`` is the pre-computed ``(buy, sell, skew_bps)`` tuple
        produced by :meth:`_compute_ideal_prices` earlier in the same cycle.
        When ``None`` (legacy callers / tests that bypass the run loop), the
        prices are computed inline. Threading the cached tuple in from the
        run loop avoids a redundant compute and double-update of the
        volatility buffer.
        """
        from order_manager import Order

        if coin in self._closer.tracked_coins:
            return

        if ideal_prices is None:
            prices = self._compute_ideal_prices(coin)
            if prices is None:
                # Distinguish "no market data" so the warning matches the
                # pre-refactor log shape used by tests/operators.
                if not self.market_data.get_market_data(coin):
                    logger.warning(f"[mm] No market data for {coin}, skipping")
                return
        else:
            prices = ideal_prices

        buy_price, sell_price, skew = prices
        if skew != 0.0:
            logger.debug(f"[mm] Inventory skew {coin}: {skew:.1f}bps")

        size = self.calculate_position_size(coin, {})
        if size <= 0:
            logger.debug(f"[mm] Position size is 0 for {coin}, skipping")
            return

        size = self.market_data.round_size(coin, size)
        if size <= 0:
            return

        current_count = self._tracker.get_order_count(coin)
        # When refresh tolerance is enabled, an order may have been kept
        # across cycles -- avoid placing a duplicate quote on the same side.
        open_sides = (
            self._tracker.get_open_sides(coin)
            if getattr(self, 'refresh_tolerance_bp', 0) > 0
            else set()
        )
        sides_and_prices = []

        # L2 book imbalance guard: skip the side that is likely to get adversely selected.
        # book_imbalance > 0 = bid-heavy (buy pressure) → selling is risky (price likely going up)
        # book_imbalance < 0 = ask-heavy (sell pressure) → buying is risky (price likely going down)
        skip_buy = False
        skip_sell = False
        if self.imbalance_threshold > 0:
            market_data = self.market_data.get_market_data(coin)
            if market_data is not None:
                imb = market_data.book_imbalance
                if imb < -self.imbalance_threshold:
                    skip_buy = True
                    logger.debug(f"[mm] {coin} skipping BUY (book imbalance {imb:.2f})")
                elif imb > self.imbalance_threshold:
                    skip_sell = True
                    logger.debug(f"[mm] {coin} skipping SELL (book imbalance {imb:.2f})")

        # Per-coin position cap: suppress same-direction entries once
        # accumulated |position| × mid_price reaches the cap. Opposite-side
        # entries are still allowed so existing inventory can unwind through
        # normal quoting. ``self._max_position_multiple == 0`` disables the
        # check entirely (legacy behaviour). ``getattr`` is used so tests
        # that bypass ``__init__`` inherit the disabled default.
        if getattr(self, '_max_position_multiple', 0.0) > 0:
            pos = self.positions.get(coin)
            if pos is not None and pos.get('size', 0) != 0:
                pos_size = float(pos['size'])
                md = self.market_data.get_market_data(coin)
                if md is not None and md.mid_price > 0:
                    coin_size_usd = self._get_coin_size(coin)
                    cap_value = self._max_position_multiple * coin_size_usd
                    pos_value = abs(pos_size) * md.mid_price
                    if pos_value >= cap_value:
                        direction = 'LONG' if pos_size > 0 else 'SHORT'
                        if pos_size > 0:
                            skip_buy = True
                        else:
                            skip_sell = True
                        logger.info(
                            f"[mm] {coin} position cap hit: |pos|=${pos_value:.0f} "
                            f"({direction}) >= cap=${cap_value:.0f} "
                            f"(={self._max_position_multiple}x size_usd) — "
                            f"skipping same-side entry"
                        )

        if (
            current_count < self.max_open_orders
            and not skip_buy
            and OrderSide.BUY.value not in open_sides
        ):
            sides_and_prices.append((OrderSide.BUY, buy_price))

        if (
            current_count + len(sides_and_prices) < self.max_open_orders
            and not skip_sell
            and OrderSide.SELL.value not in open_sides
        ):
            sides_and_prices.append((OrderSide.SELL, sell_price))

        if not sides_and_prices:
            return

        order_objects = [
            Order(
                id=None, coin=coin, side=side, size=size,
                price=price,
                order_type={"limit": {"tif": "Alo"}},
                reduce_only=False,
            )
            for side, price in sides_and_prices
        ]

        results = self.order_manager.bulk_place_orders(order_objects)

        for (side, price), order in zip(sides_and_prices, results):
            if order and order.id is not None:
                self._tracker.record_order(coin, order.id, side.value, price=price)
                self._orders_placed += 1
                self._orders_placed_per_coin[coin] += 1
                logger.info(
                    f"[mm] Placed {side.value} limit {coin} "
                    f"size={size} price={price:.6f} (oid={order.id})"
                )

    def _get_spread_prices(self, mid_price: float) -> tuple:
        """Return raw ``(buy_price, sell_price)`` based on ``spread_bps``.

        Prices are not rounded — callers apply :func:`round_price` with
        the appropriate asset parameters.
        """
        offset = mid_price * (self.spread_bps / 10_000)
        return mid_price - offset, mid_price + offset

    def _calculate_inventory_skew(self, coin: str, mid_price: float) -> float:
        """Calculate price skew in bps based on current inventory.

        Positive skew shifts both prices down (encourages selling when long).
        Negative skew shifts both prices up (encourages buying when short).
        """
        if not self.inventory_skew_bps:
            return 0.0

        position = self.positions.get(coin)
        if not position or position['size'] == 0:
            return 0.0

        size = position['size']
        if mid_price <= 0:
            return 0.0

        # Normalize position value relative to order_size_usd
        position_value = abs(size) * mid_price
        normalized = position_value / self.order_size_usd
        normalized = min(normalized, self.inventory_skew_cap)

        # Long = positive skew (shift down), Short = negative skew (shift up)
        direction = 1.0 if size > 0 else -1.0
        return direction * normalized * self.inventory_skew_bps

    # ------------------------------------------------------------------ #
    #  Fill rate observability
    # ------------------------------------------------------------------ #

    def _log_fill_rate(self) -> None:
        """Log fill rate statistics periodically."""
        now = time.monotonic()
        if now - self._last_fill_rate_log < self._fill_rate_log_interval:
            return
        self._last_fill_rate_log = now

        if self._orders_placed == 0:
            return

        fill_rate = (self._fills_detected / self._orders_placed) * 100
        logger.info(
            "[mm] Fill rate: %d/%d (%.1f%%) | per-coin fills: %s",
            self._fills_detected,
            self._orders_placed,
            fill_rate,
            dict(self._fills_per_coin) if self._fills_per_coin else "none",
        )

        # Reset counters so next log line reflects the latest window only
        self._orders_placed = 0
        self._fills_detected = 0
        self._orders_placed_per_coin.clear()
        self._fills_per_coin.clear()

    def _log_dynamic_age(self) -> None:
        """Log a periodic summary of per-coin dynamic position age.

        Emits two kinds of lines:

        1. ``[mm] Dynamic age: ...`` — last-seen ``(vol, age)`` per coin,
           preserved for backward compatibility with existing dashboards.
        2. ``[mm] dyn-age <coin> samples=N min=X% mid=X% max=X%
           raw_avg=Ns raw_range=[Ms-Ks]`` — per-coin clamp distribution
           over the interval.  ``min`` near 100% on a coin is the direct
           signal that ``DYNAMIC_AGE_MIN`` is biting.
        """
        if not getattr(self, '_dynamic_age_enabled', False):
            return
        now = time.monotonic()
        if now - self._last_dynamic_age_log < self._dynamic_age_log_interval:
            return

        if not self._dynamic_age_recent and not self._dynamic_age_clamp_stats:
            return

        # Snapshot line (existing behavior)
        if self._dynamic_age_recent:
            parts = [
                f"{coin}: vol={vol:.2f}bps age={age:.0f}s"
                for coin, (vol, age) in sorted(self._dynamic_age_recent.items())
            ]
            logger.info(f"[mm] Dynamic age: {' | '.join(parts)}")

        # Clamp stats: one line per coin, sorted by min_clamp pct desc so
        # the most clamped coin appears first.
        clamp_rows = []
        for coin, stats in self._dynamic_age_clamp_stats.items():
            samples = int(stats.get("samples", 0))
            if samples <= 0:
                continue
            min_pct = 100.0 * stats["min_clamp"] / samples
            mid_pct = 100.0 * stats["mid"] / samples
            max_pct = 100.0 * stats["max_clamp"] / samples
            raw_avg = stats["raw_sum"] / samples
            raw_min = stats["raw_min"] if stats["raw_min"] != float("inf") else raw_avg
            raw_max = stats["raw_max"]
            clamp_rows.append((coin, samples, min_pct, mid_pct, max_pct, raw_avg, raw_min, raw_max))

        clamp_rows.sort(key=lambda r: -r[2])  # min_clamp pct desc
        for coin, samples, min_pct, mid_pct, max_pct, raw_avg, raw_min, raw_max in clamp_rows:
            logger.info(
                f"[mm] dyn-age {coin} samples={samples} "
                f"min={min_pct:.0f}% mid={mid_pct:.0f}% max={max_pct:.0f}% "
                f"raw_avg={raw_avg:.0f}s raw_range=[{raw_min:.0f}s-{raw_max:.0f}s]"
            )

        self._last_dynamic_age_log = now
        # Reset so next log reflects the latest window only
        self._dynamic_age_recent.clear()
        self._dynamic_age_clamp_stats.clear()

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_coin_overrides(raw: str) -> Dict[str, float]:
        """Backward-compat shim — delegates to ``mm_config.parse_coin_overrides``.

        New code should import the helper directly. Kept here so existing
        external callers (and tests that exercised this static method) keep
        working until they migrate.
        """
        return parse_coin_overrides(raw)

    @staticmethod
    def _parse_spread_schedule(raw: str) -> Dict[int, float]:
        """Backward-compat shim — delegates to ``mm_config.parse_spread_schedule``.

        New code should import the helper directly. Kept here so existing
        external callers (and tests that exercised this static method) keep
        working until they migrate.
        """
        return parse_spread_schedule(raw)

    @staticmethod
    def _lookup_coin_override(
        coin: str, overrides: Dict[str, float], default: float,
    ) -> float:
        """Resolve a per-coin override.

        Lookup order: full ``"dex:coin"`` name → bare coin name → ``default``.
        """
        if coin in overrides:
            return overrides[coin]
        _, bare = parse_coin(coin)
        if bare in overrides:
            return overrides[bare]
        return default

    def _get_coin_offset(self, coin: str) -> float:
        """Get BBO offset for a specific coin, with optional dynamic adjustment.

        Lookup: full name → bare name → global default → dynamic adjust.
        """
        base = self._lookup_coin_override(
            coin, self._coin_offset_overrides, self.bbo_offset_bps
        )
        return self._apply_dynamic_offset(coin, base)

    def _apply_dynamic_offset(self, coin: str, base_offset: float) -> float:
        """Adjust offset based on adverse selection severity from tracker stats."""
        if not self._dynamic_offset_enabled or not self._adverse_tracker:
            return base_offset

        stats = self._adverse_tracker.stats
        coin_stats = stats.get(coin)
        if not coin_stats or coin_stats.get("fills", 0) < self._dynamic_offset_min_fills:
            return base_offset

        avg_adverse = coin_stats.get("avg_5s", 0.0)  # negative = adverse

        if avg_adverse < 0:
            adjustment = abs(avg_adverse) * self._dynamic_offset_sensitivity
        else:
            adjustment = -avg_adverse * self._dynamic_offset_tighten_rate

        adjustment = max(-self._dynamic_offset_max_reduction,
                         min(adjustment, self._dynamic_offset_max_addition))
        result = max(base_offset + adjustment, self._dynamic_offset_floor)

        if abs(adjustment) > 0.1:
            logger.debug(
                f"[mm] Dynamic offset {coin}: base={base_offset:.1f} "
                f"adj={adjustment:+.1f} → {result:.1f} (adverse_5s={avg_adverse:+.1f}bps)"
            )
        return result

    def _get_coin_spread(self, coin: str) -> float:
        """Get spread_bps for a specific coin, checking overrides first."""
        return self._lookup_coin_override(
            coin, self._coin_spread_overrides, self.spread_bps
        )

    def _get_coin_size(self, coin: str) -> float:
        """Get ORDER_SIZE_USD for a specific coin, checking overrides first."""
        return self._lookup_coin_override(
            coin, self._coin_size_overrides, self.order_size_usd
        )

    def _calculate_microprice_offsets(
        self, coin: str, base_offset_bps: float
    ) -> Tuple[float, float]:
        """Calculate asymmetric buy/sell offsets based on micro-price skew.

        Returns (buy_offset_bps, sell_offset_bps).
        When micro_price > mid (buy pressure), sell side is riskier → widen sell offset.
        When micro_price < mid (sell pressure), buy side is riskier → widen buy offset.
        """
        if not self._microprice_enabled:
            return (base_offset_bps, base_offset_bps)

        md = self.market_data.get_market_data(coin)
        if not md or md.micro_price <= 0 or md.mid_price <= 0:
            return (base_offset_bps, base_offset_bps)

        skew_bps = (md.micro_price - md.mid_price) / md.mid_price * 10_000
        skew_factor = min(
            abs(skew_bps) * self._microprice_multiplier,
            self._microprice_max_skew_bps,
        )

        if skew_bps > 0:
            # Buy pressure → sell orders at higher risk
            buy_offset = max(base_offset_bps - skew_factor * 0.5, 0.5)
            sell_offset = base_offset_bps + skew_factor
        else:
            # Sell pressure → buy orders at higher risk
            buy_offset = base_offset_bps + skew_factor
            sell_offset = max(base_offset_bps - skew_factor * 0.5, 0.5)

        return (buy_offset, sell_offset)

    def _is_quiet_hour(self) -> bool:
        """Check if the current UTC hour should stop quoting entirely."""
        hour = datetime.now(timezone.utc).hour
        if self._quiet_hours and hour in self._quiet_hours:
            return True
        # spread_schedule with multiplier 0 also triggers full-stop
        if self._spread_schedule and self._spread_schedule.get(hour, 1.0) == 0:
            return True
        return False

    def _is_drain_mode(self) -> bool:
        """Check if drain mode is active via the configured flag file."""
        if not self._drain_flag_file:
            return False
        try:
            return os.path.exists(self._drain_flag_file)
        except OSError:
            return False

    def _check_auto_exclude(self, coin: str) -> None:
        """Set ``_coin_cooldown_until[coin]`` when adverse selection has been
        moderate for ``cfg.auto_exclude.consecutive`` summary windows in a row.

        No-op when the feature is disabled, the AdverseSelectionTracker is
        unavailable, the coin is already in cooldown, or there isn't enough
        history yet. Sharing the ``_coin_cooldown_until`` map with
        ``loss_streak`` means the existing cooldown skip in ``run()`` handles
        the actual quote suppression.
        """
        # Defensive: tests that bypass __init__ (e.g. MarketMakingStrategy.__new__)
        # may not have ``cfg`` set. The feature is opt-in, so silently no-op.
        cfg_root = getattr(self, 'cfg', None)
        if cfg_root is None:
            return
        cfg = cfg_root.auto_exclude
        if not cfg.enabled or self._adverse_tracker is None:
            return
        # Already cooling down — nothing to do.
        existing_deadline = self._coin_cooldown_until.get(coin)
        if existing_deadline and existing_deadline > time.monotonic():
            return

        history = self._adverse_tracker.get_recent_windows(coin, n=cfg.consecutive)
        if len(history) < cfg.consecutive:
            return

        avg_key = f"avg_{cfg.window_label}"
        for win in history:
            if win.get("fills", 0) < cfg.min_fills:
                return
            avg = win.get(avg_key)
            if avg is None or avg > cfg.threshold_bps:
                return

        deadline = time.monotonic() + cfg.cooldown_seconds
        self._coin_cooldown_until[coin] = deadline
        logger.warning(
            f"[mm] {coin} auto-excluded: {cfg.consecutive} consecutive "
            f"{avg_key} <= {cfg.threshold_bps} bps "
            f"(min_fills={cfg.min_fills}) → cooldown {cfg.cooldown_seconds}s"
        )

    def _check_forager_health(self, coin: str) -> None:
        """Forager: composite health-score-based auto-exclude.

        Runs alongside ``_check_auto_exclude`` (markout-based) — both
        write to the shared ``_coin_cooldown_until`` map. No-op when
        the feature is disabled, the coin is already in cooldown, or
        the per-coin throttle hasn't elapsed.
        """
        cfg_root = getattr(self, 'cfg', None)
        if cfg_root is None:
            return
        cfg = cfg_root.forager
        tracker = getattr(self, '_coin_health_tracker', None)
        if not cfg.enabled or tracker is None:
            return

        # Already cooling down (auto_exclude or prior forager trigger) — skip.
        deadline = self._coin_cooldown_until.get(coin)
        now = time.monotonic()
        if deadline and deadline > now:
            return

        # Throttle: skip if checked recently for this coin.
        last_check = self._forager_last_check.get(coin, 0.0)
        if now - last_check < cfg.check_interval_seconds:
            return
        self._forager_last_check[coin] = now

        health = tracker.get_health(coin)
        # Append to the consecutive-low history; trim to ``consecutive``.
        history = self._forager_score_history[coin]
        history.append(health.composite_score)
        while len(history) > cfg.consecutive:
            history.popleft()

        if len(history) < cfg.consecutive:
            return  # not enough samples yet

        if not all(s < cfg.score_threshold for s in history):
            return

        # Avoid false positives on coins that are active but lack close
        # history yet (quality dimension undefined). The activity boundary
        # is anchored to ``_NO_HISTORY_NEUTRAL_SCORE`` so the gate scales
        # with the same midpoint the tracker uses for unknown coins —
        # adjusting one place updates both.
        if (
            health.n_closes < cfg.min_closes_for_quality
            and health.activity_score > CoinHealthTracker._NO_HISTORY_NEUTRAL_SCORE
        ):
            return

        deadline = now + cfg.cooldown_seconds
        self._coin_cooldown_until[coin] = deadline
        history.clear()  # reset; avoid immediate re-trigger after cooldown
        logger.warning(
            f"[mm] {coin} forager-excluded: composite_score="
            f"{health.composite_score:.1f} (activity={health.activity_score:.0f}, "
            f"quality={health.close_quality_score:.0f}, "
            f"cost={health.cost_score:.0f}, n_closes={health.n_closes}) "
            f"< {cfg.score_threshold} for {cfg.consecutive} checks → "
            f"cooldown {cfg.cooldown_seconds}s"
        )

    def _get_hourly_spread_multiplier(self) -> float:
        """Get spread multiplier for current UTC hour. Returns 1.0 if no schedule."""
        if not self._spread_schedule:
            return 1.0
        hour = datetime.now(timezone.utc).hour
        return self._spread_schedule.get(hour, 1.0)

    def _log_cycle(self, coins: List[str], suffix: str = "") -> None:
        """Log a cycle status line with optional suffix."""
        active_positions = sum(
            1 for coin in coins
            if coin in self.positions and self.positions[coin].get('size', 0) != 0
        )
        coin_statuses = []
        for coin in coins:
            pos = self.positions.get(coin)
            if pos and pos['size'] != 0:
                coin_statuses.append(f"{coin}:pos")
            else:
                coin_statuses.append(f"{coin}:idle")
        status_str = " | ".join(coin_statuses[:10])
        if len(coin_statuses) > 10:
            status_str += f" ... +{len(coin_statuses) - 10} more"
        logger.info(f"[cycle] {len(coins)} coins, {active_positions} pos | {status_str}{suffix}")

    @staticmethod
    def _get_risk_multiplier() -> float:
        """Read the runtime RISK_LEVEL env var and return a sizing multiplier."""
        level = os.getenv("RISK_LEVEL", "green").lower().strip()
        return {"green": 1.0, "yellow": 0.5, "red": 0.0, "black": 0.0}.get(
            level, 1.0
        )
