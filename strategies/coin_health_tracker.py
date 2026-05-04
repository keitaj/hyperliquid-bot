"""Per-coin health tracking and composite scoring for the Forager feature.

This module owns the rolling per-coin observation buffers (recent close
events plus last-fill timestamp) and computes a composite health score
in [0, 100]. The score is consumed by
``MarketMakingStrategy._check_forager_health`` to auto-pause coins that
are unfit for market making (no fills, low maker-rate close, or high
cost).

All thresholds, weights, and formula constants are read from
``ForagerConfig`` — the module contains no hardcoded numeric tunables.
"""

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Deque, Dict, Optional

if TYPE_CHECKING:
    from strategies.mm_config import ForagerConfig

logger = logging.getLogger(__name__)


@dataclass
class CloseEvent:
    """A single position close event used to compute coin health."""

    timestamp: float  # monotonic
    is_maker: bool    # True if close filled as maker
    net_pnl: float    # closed_pnl - fee
    notional: float   # size * price


@dataclass
class CoinHealth:
    """Aggregated per-coin health metrics over the rolling window."""

    activity_score: float      # 0-100, 100 = recent fill activity
    close_quality_score: float  # 0-100, 100 = all maker closes
    cost_score: float          # 0-100, 100 = $/1K vol very low
    composite_score: float     # weighted sum of the three
    n_closes: int              # closes in window
    last_fill_age: float       # seconds since last recorded fill


class CoinHealthTracker:
    """Tracks per-coin close events and computes a rolling health score.

    Thread-safe via a single lock. Used by Forager to detect coins that
    are filling poorly, bleeding cost, or completely inactive. All tuning
    parameters are sourced from the supplied ``ForagerConfig`` — there are
    no hardcoded constants in the scoring formulas, in line with the
    project policy of reading tunables via ``config.get('key', default)``.
    """

    # Score returned for the quality / cost dimensions when there is no
    # recent close history. Bias to the neutral midpoint avoids both
    # false-trigger (would happen at 0) and never-trigger (at 100).
    _NO_HISTORY_NEUTRAL_SCORE: float = 50.0

    def __init__(
        self,
        config: "ForagerConfig",
        max_events_per_coin: int = 200,
    ) -> None:
        self.config = config
        self.max_events_per_coin = max_events_per_coin
        self._closes: Dict[str, Deque[CloseEvent]] = defaultdict(
            lambda: deque(maxlen=max_events_per_coin)
        )
        self._last_fill_at: Dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Recorders
    # ------------------------------------------------------------------ #

    def record_fill(self, coin: str, ts: Optional[float] = None) -> None:
        """Update the last-fill timestamp for the activity dimension.

        Called on every fill (entry or close). Cheap; just updates a dict
        entry under the lock.
        """
        ts = ts if ts is not None else time.monotonic()
        with self._lock:
            self._last_fill_at[coin] = ts

    def record_close(
        self,
        coin: str,
        is_maker: bool,
        net_pnl: float,
        notional: float,
        ts: Optional[float] = None,
    ) -> None:
        """Append a position close event to the rolling buffer."""
        ts = ts if ts is not None else time.monotonic()
        with self._lock:
            self._closes[coin].append(
                CloseEvent(timestamp=ts, is_maker=is_maker,
                           net_pnl=net_pnl, notional=notional)
            )
            self._last_fill_at[coin] = ts

    # ------------------------------------------------------------------ #
    # Score computation
    # ------------------------------------------------------------------ #

    def get_health(self, coin: str) -> CoinHealth:
        """Compute the current composite health score for ``coin``.

        All thresholds and weights come from ``self.config``; no literals
        in this method's score formulas.
        """
        cfg = self.config
        now = time.monotonic()
        cutoff = now - cfg.window_seconds
        with self._lock:
            recent = [e for e in self._closes[coin] if e.timestamp >= cutoff]
            last_fill = self._last_fill_at.get(coin, 0.0)

        last_fill_age = (now - last_fill) if last_fill > 0 else float('inf')
        activity = self._activity_score(last_fill_age, cfg)

        n = len(recent)
        if n == 0:
            neutral = self._NO_HISTORY_NEUTRAL_SCORE
            composite = (
                activity * cfg.weight_activity
                + neutral * cfg.weight_quality
                + neutral * cfg.weight_cost
            )
            return CoinHealth(activity, neutral, neutral, composite, 0, last_fill_age)

        quality = self._close_quality_score(recent)
        cost = self._cost_score(recent, cfg)
        composite = (
            activity * cfg.weight_activity
            + quality * cfg.weight_quality
            + cost * cfg.weight_cost
        )
        return CoinHealth(activity, quality, cost, composite, n, last_fill_age)

    # ------------------------------------------------------------------ #
    # Per-axis score helpers (kept private; tests exercise via get_health)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _activity_score(last_fill_age: float, cfg: "ForagerConfig") -> float:
        """Activity dimension: 100 inside the idle grace window, decays
        linearly to 0 at ``window_seconds``."""
        idle_min = cfg.activity_idle_min_seconds
        if last_fill_age <= idle_min:
            return 100.0
        if last_fill_age >= cfg.window_seconds:
            return 0.0
        decay_range = cfg.window_seconds - idle_min
        return max(0.0, 100.0 * (1 - (last_fill_age - idle_min) / decay_range))

    @staticmethod
    def _close_quality_score(events) -> float:
        """Close-quality dimension: maker close rate × 100."""
        n = len(events)
        if n == 0:
            return 0.0
        n_maker = sum(1 for e in events if e.is_maker)
        return 100.0 * n_maker / n

    @staticmethod
    def _cost_score(events, cfg: "ForagerConfig") -> float:
        """Cost dimension: 100 at $/1K = 0, 0 at ``cost_max_per_1k``."""
        total_notional = sum(e.notional for e in events)
        if total_notional <= 0:
            return CoinHealthTracker._NO_HISTORY_NEUTRAL_SCORE
        total_loss = sum(abs(e.net_pnl) for e in events if e.net_pnl < 0)
        cost_per_1k = total_loss / (total_notional / 1000.0)
        return max(0.0, 100.0 * (1 - cost_per_1k / cfg.cost_max_per_1k))

    # ------------------------------------------------------------------ #
    # Inspection helpers (used by the strategy log + tests)
    # ------------------------------------------------------------------ #

    def tracked_coins(self) -> Dict[str, int]:
        """Return ``{coin: n_closes_in_buffer}`` snapshot for diagnostics."""
        with self._lock:
            return {c: len(d) for c, d in self._closes.items()}
