"""Configuration dataclasses for ``MarketMakingStrategy``.

Groups related MM parameters into sub-config dataclasses (loss-streak,
micro-price, velocity guard, per-coin overrides, imbalance, close,
schedule, dynamic offset, dynamic age, auto-exclude) under :class:`MMConfig`,
and exposes :meth:`MMConfig.from_legacy_dict` to build it from the existing
flat ``strategy_config`` dict.

This module also defines a handful of constants for values that were
historically read via ``config.get`` but never exposed via CLI/env. They
are kept here as a single source of truth.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---- Module-level constants (previously pseudo-config) ---- #

INVENTORY_SKEW_CAP: float = 3.0
"""Hard cap on absolute inventory-skew bps applied to quotes."""

FILL_RATE_LOG_INTERVAL: float = 300.0
"""Seconds between periodic ``[mm] Fill rate`` summary log lines."""

DYNAMIC_AGE_LOG_INTERVAL: float = 300.0
"""Seconds between periodic ``[mm] Dynamic age`` summary log lines."""


# ---- Helpers ---- #

def parse_quiet_hours(value: object) -> Set[int]:
    """Parse a ``"17,18,22"`` style hour list into a ``Set[int]``.

    Empty / falsy input returns an empty set. Non-integer entries are
    skipped after a warning log.
    """
    result: Set[int] = set()
    if not value:
        return result
    for h in str(value).split(','):
        h = h.strip()
        if not h:
            continue
        try:
            result.add(int(h))
        except ValueError:
            logger.warning(f"[mm] Invalid quiet hour value: '{h}', skipping")
    return result


def parse_spread_schedule(value: object) -> Dict[int, float]:
    """Parse spread schedule into ``{hour: multiplier}`` dict.

    Supports single hours (``"14:1.5"``) and inclusive ranges
    (``"0-3:1.5"``).  Ranges wrap around midnight (``"22-2:1.5"`` →
    hours 22, 23, 0, 1, 2).  Invalid entries are skipped after a warning.
    """
    result: Dict[int, float] = {}
    if not value or not str(value).strip():
        return result
    for entry in str(value).split(','):
        entry = entry.strip()
        if not entry:
            continue
        try:
            parts = entry.rsplit(':', 1)
            if len(parts) != 2:
                raise ValueError(f"expected 'HOUR:MULT' or 'START-END:MULT', got '{entry}'")
            hour_part, mult_str = parts
            mult = float(mult_str)
            if mult < 0:
                raise ValueError(f"multiplier must be >= 0, got {mult}")

            if '-' in hour_part:
                start_str, end_str = hour_part.split('-', 1)
                start_hour = int(start_str.strip())
                end_hour = int(end_str.strip())
                if not (0 <= start_hour <= 23):
                    raise ValueError(f"start hour must be 0-23, got {start_hour}")
                if not (0 <= end_hour <= 23):
                    raise ValueError(f"end hour must be 0-23, got {end_hour}")
                h = start_hour
                while True:
                    result[h] = mult
                    if h == end_hour:
                        break
                    h = (h + 1) % 24
            else:
                hour = int(hour_part.strip())
                if not (0 <= hour <= 23):
                    raise ValueError(f"hour must be 0-23, got {hour}")
                result[hour] = mult
        except (ValueError, IndexError) as e:
            logger.warning(f"[mm] Invalid spread_schedule entry: '{entry}', skipping ({e})")
    return result


def parse_coin_overrides(value: object) -> Dict[str, float]:
    """Parse a ``"COIN:BPS,COIN:BPS,..."`` string into ``{coin: bps}``.

    Supports both bare names (``"SP500:1.5"``) and DEX-prefixed names
    (``"xyz:SP500:1.5"``); bare names match any DEX at lookup time.
    Empty / falsy input returns an empty dict. Pairs with missing colons
    or non-numeric values are skipped after a single warning log per
    offending pair.
    """
    result: Dict[str, float] = {}
    if not value or not str(value).strip():
        return result
    for pair in str(value).split(','):
        pair = pair.strip()
        if not pair:
            continue
        parts = pair.rsplit(':', 1)
        if len(parts) != 2:
            logger.warning(f"[mm] Invalid coin override format: '{pair}', expected 'COIN:BPS'")
            continue
        coin_key, bps_str = parts
        try:
            result[coin_key] = float(bps_str)
        except ValueError:
            logger.warning(f"[mm] Invalid BPS value in coin override: '{pair}'")
    return result


def parse_coin_tier_overrides(value: object) -> Dict[str, Tuple[float, float]]:
    """Parse ``"COIN:BREAKEVEN/AGGRESSIVE,..."`` into ``{coin: (b_pct, a_pct)}``.

    Example: ``"NVDA:0.30/0.55,xyz:XYZ100:0.40/0.65"``. Supports both bare
    names and DEX-prefixed names, same as :func:`parse_coin_overrides`.
    Each pair must satisfy ``0 <= breakeven < aggressive <= 1``; invalid
    pairs are skipped after a warning log, valid pairs survive.
    """
    result: Dict[str, Tuple[float, float]] = {}
    if not value or not str(value).strip():
        return result
    for pair in str(value).split(','):
        pair = pair.strip()
        if not pair:
            continue
        # rpartition keeps DEX-prefixed keys intact ("xyz:XYZ100:0.4/0.65")
        coin_key, _, val = pair.rpartition(':')
        try:
            if not coin_key:
                raise ValueError("missing coin name")
            b_str, a_str = val.split('/')
            b, a = float(b_str), float(a_str)
            if not (0.0 <= b < a <= 1.0):
                raise ValueError(
                    f"require 0 <= breakeven < aggressive <= 1, got {b}/{a}"
                )
            result[coin_key] = (b, a)
        except ValueError as e:
            logger.warning(f"[mm] Invalid close tier override: '{pair}' ({e})")
    return result


# ---- Dataclass groups ---- #

@dataclass
class LossStreakConfig:
    """Per-coin cooldown after consecutive losing closes.

    A ``limit`` of 0 disables the feature.
    """

    limit: int = 0
    cooldown_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise ValueError(f"loss_streak_limit must be >= 0, got {self.limit}")
        if self.limit > 0 and self.cooldown_seconds <= 0:
            raise ValueError(
                f"loss_streak_cooldown must be > 0 when limit is set, "
                f"got {self.cooldown_seconds}"
            )


@dataclass
class MicropriceConfig:
    """Asymmetric quote offset based on book micro-price skew."""

    enabled: bool = False
    multiplier: float = 1.0
    max_skew_bps: float = 2.0


@dataclass
class VelocityGuardConfig:
    """Cancel orders on consecutive directional BBO moves.

    Read by ``bot.py`` via ``strategy.cfg.velocity`` when wiring the WS
    ``BboVelocityGuard``; the MM strategy itself does not read these fields.
    """

    enabled: bool = False
    consecutive: int = 3
    min_move_bps: float = 1.0


@dataclass
class OracleGuardConfig:
    """Oracle divergence / momentum guard (HIP-3 ``oraclePx`` stream).

    Read by ``bot.py`` via ``strategy.cfg.oracle_guard`` when wiring the
    WS ``OracleDivergenceGuard``.  The strategy only consumes the guard's
    ``get_blocked_sides`` hook via injection.  ``enabled`` defaults to
    ``False`` — no subscription or guard object is created, preserving
    the pre-guard behaviour exactly.
    """

    enabled: bool = False
    divergence_threshold_bps: float = 5.0
    momentum_cap_pin_bps: float = 80.0
    momentum_min_step_bps: float = 5.0
    momentum_consecutive: int = 3
    stale_ttl_seconds: float = 30.0
    block_seconds: float = 10.0
    # env/JSON-only knob (no CLI flag) — formula constant, rarely tuned
    min_cancel_interval: float = 2.0

    def __post_init__(self) -> None:
        for name in ("divergence_threshold_bps", "momentum_cap_pin_bps",
                     "momentum_min_step_bps"):
            if getattr(self, name) < 0:
                raise ValueError(
                    f"oracle_{name} must be >= 0, got {getattr(self, name)}"
                )
        if self.momentum_consecutive < 0:
            raise ValueError(
                f"oracle_momentum_consecutive must be >= 0, "
                f"got {self.momentum_consecutive}"
            )
        if self.stale_ttl_seconds <= 0:
            raise ValueError(
                f"oracle_stale_ttl_seconds must be > 0, got {self.stale_ttl_seconds}"
            )
        if self.block_seconds < 0:
            raise ValueError(
                f"oracle_guard_block_seconds must be >= 0, got {self.block_seconds}"
            )
        if self.min_cancel_interval < 0:
            raise ValueError(
                f"oracle_guard_min_cancel_interval must be >= 0, "
                f"got {self.min_cancel_interval}"
            )


@dataclass
class PerCoinOverrides:
    """Per-coin overrides for offset, spread, order size, unrealized-loss
    early-close threshold, and close tier transition timing."""

    offset: Dict[str, float] = field(default_factory=dict)
    spread: Dict[str, float] = field(default_factory=dict)
    size: Dict[str, float] = field(default_factory=dict)
    unrealized_loss: Dict[str, float] = field(default_factory=dict)
    # coin -> (breakeven_pct, aggressive_pct)
    close_tier: Dict[str, Tuple[float, float]] = field(default_factory=dict)


@dataclass
class ImbalanceConfig:
    """L2 book imbalance thresholds.

    ``placement_threshold`` is read by the MM strategy at order-placement
    time (skip the side that's adversely-imbalanced).  ``reactive_threshold``
    and ``reactive_depth`` are read by ``bot.py`` to wire up the WS
    ``ImbalanceGuard`` (cancel orders when the book skews after placement).
    """

    placement_threshold: float = 0.0
    reactive_threshold: float = 0.0
    reactive_depth: int = 5

    def __post_init__(self) -> None:
        if not (0 <= self.placement_threshold <= 1):
            raise ValueError(
                f"imbalance_threshold must be in [0, 1], got {self.placement_threshold}"
            )


@dataclass
class CloseConfig:
    """Position-close behaviour parameters.

    Most fields default to disabled (``0.0`` / ``None``) so the legacy
    behaviour is preserved when no value is provided.
    """

    breakeven_pct: float = 0.50
    aggressive_pct: float = 0.75
    spread_bps: Optional[float] = None
    refresh_threshold_bps: float = 0.0
    unrealized_loss_close_bps: float = 0.0
    force_close_max_loss_bps: float = 0.0
    # Absolute floor (seconds) for the breakeven tier transition. Guards
    # against over-shortening when dynamic age, per-coin overrides and
    # toxicity acceleration stack. 0 = disabled (legacy behaviour).
    tier_min_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.tier_min_seconds < 0:
            raise ValueError(
                f"close_tier_min_seconds must be >= 0, got {self.tier_min_seconds}"
            )


@dataclass
class CloseTierToxicityConfig:
    """Toxicity-linked close tier acceleration.

    When the recent-window average markout for a coin (from
    ``AdverseSelectionTracker``) is at or below ``threshold_bps``, the
    coin's tier percentages are multiplied by ``multiplier`` so the close
    order concedes to breakeven/aggressive earlier — increasing the chance
    of a maker exit before the taker force-close fires. Requires the
    tracker (``--enable-adverse-selection-log`` + WebSocket); falls back
    to base behaviour when unavailable.
    """

    enabled: bool = False
    threshold_bps: float = -2.0
    window: str = "30s"
    multiplier: float = 0.6
    min_fills: int = 5
    floor_pct: float = 0.15

    def __post_init__(self) -> None:
        if self.threshold_bps > 0:
            raise ValueError(
                f"close_tier_toxicity_threshold_bps must be <= 0 "
                f"(negative = adverse), got {self.threshold_bps}"
            )
        if not (0.1 <= self.multiplier <= 1.0):
            raise ValueError(
                f"close_tier_toxicity_multiplier must be in [0.1, 1.0], "
                f"got {self.multiplier}"
            )
        if self.window not in ("5s", "30s", "60s"):
            raise ValueError(
                f"close_tier_toxicity_window must be one of '5s'/'30s'/'60s', "
                f"got {self.window!r}"
            )
        if self.min_fills < 1:
            raise ValueError(
                f"close_tier_toxicity_min_fills must be >= 1, got {self.min_fills}"
            )
        if not (0.0 <= self.floor_pct <= 1.0):
            raise ValueError(
                f"close_tier_toxicity_floor_pct must be in [0, 1], "
                f"got {self.floor_pct}"
            )


@dataclass
class ScheduleConfig:
    """Hourly spread schedule and quiet-hours behaviour."""

    spread_schedule: Dict[int, float] = field(default_factory=dict)
    quiet_hours_utc: Set[int] = field(default_factory=set)
    quiet_hours_spread_multiplier: float = 0.0


@dataclass
class DynamicOffsetConfig:
    """Auto-adjust BBO offset from observed adverse-selection data.

    The strategy widens the offset when recent fills look adversely selected
    and tightens it after favourable fills, bounded by the floor and the
    asymmetric ``max_addition`` / ``max_reduction`` caps.  Activates only
    after ``min_fills`` samples are accumulated for a coin.
    """

    enabled: bool = False
    sensitivity: float = 0.5
    tighten_rate: float = 0.25
    max_addition: float = 3.0
    max_reduction: float = 1.0
    floor: float = 0.5
    min_fills: int = 5


@dataclass
class DynamicAgeConfig:
    """Volatility-adjusted ``MAX_POSITION_AGE``.

    Recent mid-price volatility scales the per-coin position age between
    ``min_seconds`` (high vol) and ``max_seconds`` (low vol), pivoting
    around ``baseline_vol_bps``.
    """

    enabled: bool = False
    baseline_vol_bps: float = 1.0
    min_seconds: float = 60.0
    max_seconds: float = 300.0


@dataclass
class AutoExcludeConfig:
    """Auto-exclude a coin after consecutive adverse-selection windows.

    Reads observations from ``AdverseSelectionTracker``'s recent-window
    history; when ``consecutive`` summary windows in a row show
    ``avg_<window_label>`` at or below ``threshold_bps`` (with at least
    ``min_fills`` per window), the coin is paused for
    ``cooldown_seconds`` via the existing ``_coin_cooldown_until`` map
    shared with ``LossStreakConfig``.
    """

    enabled: bool = False
    threshold_bps: float = -3.0
    consecutive: int = 3
    min_fills: int = 3
    cooldown_seconds: int = 1800
    window_label: str = "60s"

    def __post_init__(self) -> None:
        if self.consecutive < 1:
            raise ValueError(
                f"auto_exclude_consecutive must be >= 1, got {self.consecutive}"
            )
        if self.min_fills < 1:
            raise ValueError(
                f"auto_exclude_min_fills must be >= 1, got {self.min_fills}"
            )
        if self.cooldown_seconds <= 0:
            raise ValueError(
                f"auto_exclude_cooldown must be > 0, got {self.cooldown_seconds}"
            )
        if self.window_label not in ("5s", "30s", "60s"):
            raise ValueError(
                f"auto_exclude_window_label must be one of '5s'/'30s'/'60s', "
                f"got {self.window_label!r}"
            )


@dataclass
class PositionCapConfig:
    """Per-coin position size cap on the entry side.

    When the accumulated position value (|size| × mid_price) reaches
    ``max_position_multiple`` × effective ``order_size_usd`` for the coin,
    same-direction entries are suppressed (opposite-direction entries are
    still allowed so the inventory can naturally unwind through normal
    quoting).

    A value of ``0.0`` (default) disables the cap entirely, preserving the
    pre-cap behaviour.
    """

    max_position_multiple: float = 0.0

    def __post_init__(self) -> None:
        if self.max_position_multiple < 0:
            raise ValueError(
                f"max_position_multiple must be >= 0, got {self.max_position_multiple}"
            )


@dataclass
class ForagerConfig:
    """Multi-axis coin health scoring for auto-exclude.

    Complements :class:`AutoExcludeConfig` (markout-based) with three
    additional signals: activity (fill frequency), close-quality
    (maker rate), and cost ($/1K vol). The composite score (0-100,
    higher is healthier) drops below ``score_threshold`` for
    ``consecutive`` consecutive checks → coin is paused via the existing
    ``_coin_cooldown_until`` map shared with :class:`AutoExcludeConfig`
    and :class:`LossStreakConfig`.

    Defaults match ``docs/design-doc/20260504_forager_coin_health.md``.
    """

    enabled: bool = False
    score_threshold: float = 30.0
    consecutive: int = 3
    cooldown_seconds: int = 1800
    weight_activity: float = 0.3
    weight_quality: float = 0.4
    weight_cost: float = 0.3
    # env-only knobs (no CLI flag) — formula constants, rarely tuned
    window_seconds: float = 1800.0
    check_interval_seconds: float = 300.0
    activity_idle_min_seconds: float = 300.0
    cost_max_per_1k: float = 0.6
    min_closes_for_quality: int = 5

    def __post_init__(self) -> None:
        if not 0.0 <= self.score_threshold <= 100.0:
            raise ValueError(
                f"forager_score_threshold must be in [0, 100], got {self.score_threshold}"
            )
        if self.consecutive < 1:
            raise ValueError(
                f"forager_consecutive must be >= 1, got {self.consecutive}"
            )
        if self.cooldown_seconds <= 0:
            raise ValueError(
                f"forager_cooldown_seconds must be > 0, got {self.cooldown_seconds}"
            )
        for name, val in (
            ("weight_activity", self.weight_activity),
            ("weight_quality", self.weight_quality),
            ("weight_cost", self.weight_cost),
        ):
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"forager_{name} must be in [0, 1], got {val}")
        if self.window_seconds <= 0:
            raise ValueError(
                f"forager_window_seconds must be > 0, got {self.window_seconds}"
            )
        if self.check_interval_seconds < 0:
            raise ValueError(
                f"forager_check_interval_seconds must be >= 0, got {self.check_interval_seconds}"
            )
        if self.activity_idle_min_seconds < 0:
            raise ValueError(
                f"forager_activity_idle_min_seconds must be >= 0, "
                f"got {self.activity_idle_min_seconds}"
            )
        if self.activity_idle_min_seconds >= self.window_seconds:
            raise ValueError(
                f"forager_activity_idle_min_seconds ({self.activity_idle_min_seconds}) "
                f"must be < window_seconds ({self.window_seconds})"
            )
        if self.cost_max_per_1k <= 0:
            raise ValueError(
                f"forager_cost_max_per_1k must be > 0, got {self.cost_max_per_1k}"
            )
        if self.min_closes_for_quality < 1:
            raise ValueError(
                f"forager_min_closes_for_quality must be >= 1, "
                f"got {self.min_closes_for_quality}"
            )


@dataclass
class MMConfig:
    """Root config for ``MarketMakingStrategy``.

    Aggregates all grouped sub-configs that were originally read directly
    from the flat ``strategy_config`` dict.  See :meth:`from_legacy_dict`
    for the bridge from the legacy flat format.
    """

    loss_streak: LossStreakConfig = field(default_factory=LossStreakConfig)
    microprice: MicropriceConfig = field(default_factory=MicropriceConfig)
    velocity: VelocityGuardConfig = field(default_factory=VelocityGuardConfig)
    oracle_guard: OracleGuardConfig = field(default_factory=OracleGuardConfig)
    per_coin: PerCoinOverrides = field(default_factory=PerCoinOverrides)
    imbalance: ImbalanceConfig = field(default_factory=ImbalanceConfig)
    close: CloseConfig = field(default_factory=CloseConfig)
    close_tier_toxicity: CloseTierToxicityConfig = field(
        default_factory=CloseTierToxicityConfig
    )
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    dynamic_offset: DynamicOffsetConfig = field(default_factory=DynamicOffsetConfig)
    dynamic_age: DynamicAgeConfig = field(default_factory=DynamicAgeConfig)
    auto_exclude: AutoExcludeConfig = field(default_factory=AutoExcludeConfig)
    forager: ForagerConfig = field(default_factory=ForagerConfig)
    position_cap: PositionCapConfig = field(default_factory=PositionCapConfig)

    @classmethod
    def from_legacy_dict(cls, d: Dict) -> "MMConfig":
        """Build :class:`MMConfig` from the existing flat ``strategy_config``.

        Unknown keys are ignored — this method is a one-way bridge used while
        the rest of the bot still populates a flat dict.
        """
        close_spread_bps_raw = d.get('close_spread_bps', None)
        close_spread_bps = float(close_spread_bps_raw) if close_spread_bps_raw is not None else None
        return cls(
            loss_streak=LossStreakConfig(
                limit=int(d.get('loss_streak_limit', 0)),
                cooldown_seconds=float(d.get('loss_streak_cooldown', 300)),
            ),
            microprice=MicropriceConfig(
                enabled=bool(d.get('microprice_skew_enabled', False)),
                multiplier=float(d.get('microprice_skew_multiplier', 1.0)),
                max_skew_bps=float(d.get('microprice_max_skew_bps', 2.0)),
            ),
            velocity=VelocityGuardConfig(
                enabled=bool(d.get('velocity_guard_enabled', False)),
                consecutive=int(d.get('velocity_consecutive', 3)),
                min_move_bps=float(d.get('velocity_min_move_bps', 1.0)),
            ),
            oracle_guard=OracleGuardConfig(
                enabled=bool(d.get('oracle_guard_enabled', False)),
                divergence_threshold_bps=float(d.get('oracle_divergence_threshold_bps', 5.0)),
                momentum_cap_pin_bps=float(d.get('oracle_momentum_cap_pin_bps', 80.0)),
                momentum_min_step_bps=float(d.get('oracle_momentum_min_step_bps', 5.0)),
                momentum_consecutive=int(d.get('oracle_momentum_consecutive', 3)),
                stale_ttl_seconds=float(d.get('oracle_stale_ttl_seconds', 30.0)),
                block_seconds=float(d.get('oracle_guard_block_seconds', 10.0)),
                min_cancel_interval=float(d.get('oracle_guard_min_cancel_interval', 2.0)),
            ),
            per_coin=PerCoinOverrides(
                offset=parse_coin_overrides(d.get('coin_offset_overrides', '')),
                spread=parse_coin_overrides(d.get('coin_spread_overrides', '')),
                size=parse_coin_overrides(d.get('coin_size_overrides', '')),
                unrealized_loss=parse_coin_overrides(d.get('coin_unrealized_loss_overrides', '')),
                close_tier=parse_coin_tier_overrides(d.get('coin_close_tier_overrides', '')),
            ),
            imbalance=ImbalanceConfig(
                placement_threshold=float(d.get('imbalance_threshold', 0.0)),
                reactive_threshold=float(d.get('imbalance_guard_threshold', 0.0)),
                reactive_depth=int(d.get('imbalance_guard_depth', 5)),
            ),
            close=CloseConfig(
                breakeven_pct=float(d.get('close_breakeven_pct', 0.50)),
                aggressive_pct=float(d.get('close_aggressive_pct', 0.75)),
                spread_bps=close_spread_bps,
                refresh_threshold_bps=float(d.get('close_refresh_threshold_bps', 0.0)),
                unrealized_loss_close_bps=float(d.get('unrealized_loss_close_bps', 0.0)),
                force_close_max_loss_bps=float(d.get('force_close_max_loss_bps', 0.0)),
                tier_min_seconds=float(d.get('close_tier_min_seconds', 0.0)),
            ),
            close_tier_toxicity=CloseTierToxicityConfig(
                enabled=bool(d.get('close_tier_toxicity_enabled', False)),
                threshold_bps=float(d.get('close_tier_toxicity_threshold_bps', -2.0)),
                window=str(d.get('close_tier_toxicity_window', '30s')),
                multiplier=float(d.get('close_tier_toxicity_multiplier', 0.6)),
                min_fills=int(d.get('close_tier_toxicity_min_fills', 5)),
                floor_pct=float(d.get('close_tier_toxicity_floor_pct', 0.15)),
            ),
            schedule=ScheduleConfig(
                spread_schedule=parse_spread_schedule(d.get('spread_schedule', '')),
                quiet_hours_utc=parse_quiet_hours(d.get('quiet_hours_utc', '')),
                quiet_hours_spread_multiplier=float(d.get('quiet_hours_spread_multiplier', 0.0)),
            ),
            dynamic_offset=DynamicOffsetConfig(
                enabled=bool(d.get('dynamic_offset_enabled', False)),
                sensitivity=float(d.get('dynamic_offset_sensitivity', 0.5)),
                tighten_rate=float(d.get('dynamic_offset_tighten_rate', 0.25)),
                max_addition=float(d.get('dynamic_offset_max_addition', 3.0)),
                max_reduction=float(d.get('dynamic_offset_max_reduction', 1.0)),
                floor=float(d.get('dynamic_offset_floor', 0.5)),
                min_fills=int(d.get('dynamic_offset_min_fills', 5)),
            ),
            dynamic_age=DynamicAgeConfig(
                enabled=bool(d.get('dynamic_age_enabled', False)),
                baseline_vol_bps=float(d.get('dynamic_age_baseline_vol', 1.0)),
                min_seconds=float(d.get('dynamic_age_min', 60.0)),
                max_seconds=float(d.get('dynamic_age_max', 300.0)),
            ),
            auto_exclude=AutoExcludeConfig(
                enabled=bool(d.get('auto_exclude_enabled', False)),
                threshold_bps=float(d.get('auto_exclude_threshold_bps', -3.0)),
                consecutive=int(d.get('auto_exclude_consecutive', 3)),
                min_fills=int(d.get('auto_exclude_min_fills', 3)),
                cooldown_seconds=int(d.get('auto_exclude_cooldown', 1800)),
                window_label=str(d.get('auto_exclude_window_label', '60s')),
            ),
            forager=ForagerConfig(
                enabled=bool(d.get('forager_enabled', False)),
                score_threshold=float(d.get('forager_score_threshold', 30.0)),
                consecutive=int(d.get('forager_consecutive', 3)),
                cooldown_seconds=int(d.get('forager_cooldown_seconds', 1800)),
                weight_activity=float(d.get('forager_weight_activity', 0.3)),
                weight_quality=float(d.get('forager_weight_quality', 0.4)),
                weight_cost=float(d.get('forager_weight_cost', 0.3)),
                window_seconds=float(d.get('forager_window_seconds', 1800.0)),
                check_interval_seconds=float(
                    d.get('forager_check_interval_seconds', 300.0)
                ),
                activity_idle_min_seconds=float(
                    d.get('forager_activity_idle_min_seconds', 300.0)
                ),
                cost_max_per_1k=float(d.get('forager_cost_max_per_1k', 0.6)),
                min_closes_for_quality=int(
                    d.get('forager_min_closes_for_quality', 5)
                ),
            ),
            position_cap=PositionCapConfig(
                max_position_multiple=float(d.get('max_position_multiple', 0.0)),
            ),
        )
