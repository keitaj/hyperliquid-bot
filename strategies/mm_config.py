"""Configuration dataclasses for ``MarketMakingStrategy``.

Phase 1 of the MM config refactor: groups four sets of related parameters
(loss-streak cooldown, micro-price skew, BBO velocity guard, per-coin
overrides) into dataclasses and exposes a :meth:`MMConfig.from_legacy_dict`
constructor for the existing flat ``strategy_config`` dict.

This module also defines a handful of constants for values that were
historically read via ``config.get`` but never exposed via CLI/env. They
are kept here as a single source of truth.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict

logger = logging.getLogger(__name__)


# ---- Module-level constants (previously pseudo-config) ---- #

INVENTORY_SKEW_CAP: float = 3.0
"""Hard cap on absolute inventory-skew bps applied to quotes."""

FILL_RATE_LOG_INTERVAL: float = 300.0
"""Seconds between periodic ``[mm] Fill rate`` summary log lines."""

DYNAMIC_AGE_LOG_INTERVAL: float = 300.0
"""Seconds between periodic ``[mm] Dynamic age`` summary log lines."""


# ---- Helpers ---- #

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
class PerCoinOverrides:
    """Per-coin overrides for offset, spread, and order size."""

    offset: Dict[str, float] = field(default_factory=dict)
    spread: Dict[str, float] = field(default_factory=dict)
    size: Dict[str, float] = field(default_factory=dict)


@dataclass
class MMConfig:
    """Root config for ``MarketMakingStrategy`` (Phase 1 subset).

    Future phases will fold additional groups (close, dynamic offset,
    dynamic age, imbalance, schedule) into this same root.
    """

    loss_streak: LossStreakConfig = field(default_factory=LossStreakConfig)
    microprice: MicropriceConfig = field(default_factory=MicropriceConfig)
    velocity: VelocityGuardConfig = field(default_factory=VelocityGuardConfig)
    per_coin: PerCoinOverrides = field(default_factory=PerCoinOverrides)

    @classmethod
    def from_legacy_dict(cls, d: Dict) -> "MMConfig":
        """Build :class:`MMConfig` from the existing flat ``strategy_config``.

        Unknown keys are ignored — this method is a one-way bridge used while
        the rest of the bot still populates a flat dict.
        """
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
            per_coin=PerCoinOverrides(
                offset=parse_coin_overrides(d.get('coin_offset_overrides', '')),
                spread=parse_coin_overrides(d.get('coin_spread_overrides', '')),
                size=parse_coin_overrides(d.get('coin_size_overrides', '')),
            ),
        )
