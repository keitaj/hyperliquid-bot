"""Adverse selection measurement for market-making fills.

Tracks mid-price movement after each fill to quantify adverse selection
per coin.  Outputs periodic summary logs for operational analysis.

This is an observation-only module — it does not affect trading logic.

Usage::

    tracker = AdverseSelectionTracker(market_data_manager)
    fill_feed.set_adverse_selection_tracker(tracker)
    ...
    tracker.stop()
"""

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Sample intervals (seconds after fill)
SAMPLE_INTERVALS = [5, 30, 60]
SAMPLE_LABELS = ["5s", "30s", "60s"]

# Max fills to keep in memory
MAX_FILL_BUFFER = 200


@dataclass
class FillSnapshot:
    """Single fill with post-fill price samples."""

    fill_id: str           # unique id (coin + timestamp)
    coin: str
    side: str              # "A" (sell) or "B" (buy)
    fill_px: float
    mid_at_fill: float
    fill_time: float       # monotonic time
    wall_time: float       # time.time() for logging
    samples: Dict[str, float] = field(default_factory=dict)  # label -> adverse_bps


class AdverseSelectionTracker:
    """Track post-fill price movement to measure adverse selection."""

    def __init__(
        self,
        market_data: Any,
        log_interval: float = 300.0,
    ) -> None:
        self.market_data = market_data
        self.log_interval = log_interval

        self._fills: deque = deque(maxlen=MAX_FILL_BUFFER)
        self._lock = threading.Lock()
        self._running = True
        self._last_log_time = time.monotonic()

        # Aggregates for periodic logging: coin -> label -> list of adverse_bps
        self._aggregates: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._fill_count: Dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ #
    #  Fill recording
    # ------------------------------------------------------------------ #

    def on_fill(
        self,
        coin: str,
        fill_px: float,
        side: str,
        fill_time_ms: Optional[int] = None,
    ) -> None:
        """Called from FillFeed when a fill occurs.

        Parameters
        ----------
        coin : str
            Coin name (e.g. ``'xyz:SP500'``)
        fill_px : float
            Fill price
        side : str
            ``'A'`` (sell) or ``'B'`` (buy)
        fill_time_ms : int, optional
            Exchange fill timestamp in ms (unused, reserved for future).
        """
        if not self._running:
            return

        try:
            md = self.market_data.get_market_data(coin)
            if not md or md.mid_price <= 0:
                return

            mid_at_fill = md.mid_price
            now_mono = time.monotonic()
            now_wall = time.time()

            fill_id = f"{coin}_{now_wall:.3f}"
            snapshot = FillSnapshot(
                fill_id=fill_id,
                coin=coin,
                side=side,
                fill_px=fill_px,
                mid_at_fill=mid_at_fill,
                fill_time=now_mono,
                wall_time=now_wall,
            )

            with self._lock:
                self._fills.append(snapshot)
                self._fill_count[coin] += 1

            # Schedule delayed samples
            for delay, label in zip(SAMPLE_INTERVALS, SAMPLE_LABELS):
                timer = threading.Timer(delay, self._sample_mid, args=(snapshot, label))
                timer.daemon = True
                timer.start()

            spread_to_mid_bps = (fill_px - mid_at_fill) / mid_at_fill * 10_000
            logger.debug(
                "[adverse] Fill %s %s px=%.6f mid=%.6f spread=%.1fbps",
                side, coin, fill_px, mid_at_fill, spread_to_mid_bps,
            )

        except Exception as e:
            logger.error("[adverse] Error recording fill: %s", e)

    # ------------------------------------------------------------------ #
    #  Delayed sampling
    # ------------------------------------------------------------------ #

    def _sample_mid(self, snapshot: FillSnapshot, label: str) -> None:
        """Delayed callback to sample mid price and compute adverse selection."""
        if not self._running:
            return
        try:
            md = self.market_data.get_market_data(snapshot.coin)
            if not md or md.mid_price <= 0:
                return

            mid_now = md.mid_price
            mid_at_fill = snapshot.mid_at_fill

            # Adverse selection: how much did mid move AGAINST the filled side?
            # BUY: we bought → mid going UP after = adverse (price moved away)
            # SELL: we sold → mid going DOWN after = adverse
            # Convention: negative = adverse, positive = favorable
            direction = -1 if snapshot.side == "B" else 1
            adverse_bps = direction * (mid_now - mid_at_fill) / mid_at_fill * 10_000
            snapshot.samples[label] = adverse_bps

            with self._lock:
                self._aggregates[snapshot.coin][label].append(adverse_bps)

            logger.debug(
                "[adverse] %s %s %s: mid %.6f → %.6f = %+.1f bps",
                label, snapshot.side, snapshot.coin,
                mid_at_fill, mid_now, adverse_bps,
            )

        except Exception as e:
            logger.error("[adverse] Error sampling mid for %s: %s", snapshot.coin, e)

    # ------------------------------------------------------------------ #
    #  Periodic summary
    # ------------------------------------------------------------------ #

    def maybe_log_summary(self) -> None:
        """Log periodic summary if interval has elapsed.

        Should be called from the main loop.
        """
        now = time.monotonic()
        if now - self._last_log_time < self.log_interval:
            return
        self._last_log_time = now
        self._log_summary()

    def _log_summary(self) -> None:
        """Log per-coin adverse selection summary and reset aggregates."""
        with self._lock:
            aggregates = dict(self._aggregates)
            fill_counts = dict(self._fill_count)
            self._aggregates = defaultdict(lambda: defaultdict(list))
            self._fill_count = defaultdict(int)

        if not aggregates:
            return

        lines = [f"[adverse] Summary (last {self.log_interval:.0f}s):"]
        for coin in sorted(aggregates.keys()):
            fills = fill_counts.get(coin, 0)
            parts = [f"  {coin}: fills={fills}"]
            for label in SAMPLE_LABELS:
                values = aggregates[coin].get(label, [])
                if values:
                    avg = sum(values) / len(values)
                    parts.append(f"avg_{label}={avg:+.1f}bps")
                else:
                    parts.append(f"avg_{label}=n/a")
            lines.append("  ".join(parts))

        logger.info("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def stop(self) -> None:
        """Stop tracker and log final summary."""
        self._running = False
        self._log_summary()
        logger.info("[adverse] Stopped")

    # ------------------------------------------------------------------ #
    #  Observability
    # ------------------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> Dict:
        """Return current aggregate stats for external consumption."""
        with self._lock:
            result: Dict[str, Any] = {}
            for coin, labels in self._aggregates.items():
                coin_stats: Dict[str, Any] = {"fills": self._fill_count.get(coin, 0)}
                for label in SAMPLE_LABELS:
                    values = labels.get(label, [])
                    if values:
                        coin_stats[f"avg_{label}"] = sum(values) / len(values)
                result[coin] = coin_stats
            return result
