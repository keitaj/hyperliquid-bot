"""Order rejection classification and aggregation.

The bot's order placement path (``order_manager.py``) historically emits
an ERROR-level log for *every* rejection returned by the Hyperliquid
exchange, regardless of whether the rejection signals a routine retry
condition (e.g. post-only orders that would have crossed the BBO) or a
genuine error condition. Under live MM load the routine post-only
rejection accounts for nearly all ERROR records, drowning out real
anomalies in monitoring.

This module reclassifies each rejection by matching the API error text
against a static set of known "routine" patterns. Matched rejections can
be downgraded to WARNING / INFO via config, and the tracker accumulates
per-coin counts that are flushed as a single summary line every
``rejection_summary_interval`` seconds.

The tracker is *opt-in*: ``order_manager.py`` only invokes it when one
has been registered via ``set_rejection_tracker``. With no tracker the
legacy ERROR-level path is preserved exactly.

Public surface:

* :class:`OrderRejectionTracker` — main tracker.
* :func:`classify_rejection` — pure helper used by tests.

Both are intentionally cheap on the order-placement hot path: ``record``
is O(1) under a short-lived lock, and the periodic summary is driven
externally by the strategy's main loop (no background thread).
"""

import logging
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# Pattern matcher → tag. Patterns are evaluated in order; current entries
# are mutually exclusive on the API's error text format. Adding a new
# routine pattern (e.g. ``Reduce only``) is a one-line edit here plus a
# regression test in ``tests/test_order_rejection_tracker.py``.
_ROUTINE_PATTERNS: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"Post only order would have immediately matched"),
     "post_only_match"),
)

# Mapping from log-level config string to logging level constant. Keys
# are normalised to lowercase before lookup so config values like
# ``"WARNING"`` continue to work.
_LEVEL_MAP: Dict[str, int] = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

# Allowed config string values, exposed for validators.
ALLOWED_LOG_LEVELS = tuple(_LEVEL_MAP.keys())

# Tag used for unmatched rejection text. Always logged at ERROR so a
# format change on the exchange surfaces immediately.
UNKNOWN_TAG = "unknown"


def classify_rejection(msg: str) -> str:
    """Return the routine tag for *msg*, or :data:`UNKNOWN_TAG` if none match.

    Pure function exposed for tests so the pattern map can be exercised
    without instantiating a tracker.
    """
    for pat, tag in _ROUTINE_PATTERNS:
        if pat.search(msg):
            return tag
    return UNKNOWN_TAG


def _extract_bbo(msg: str) -> str:
    """Best-effort extraction of the ``"bid@ask"`` snippet from a reject text.

    Returns an empty string when the pattern is absent (e.g. older HL
    error messages or future format changes).
    """
    m = re.search(r"bbo was ([\d.]+@[\d.]+)", msg)
    return m.group(1) if m else ""


@dataclass
class _CoinStats:
    count: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    last_bbo: str = ""


class OrderRejectionTracker:
    """Classify and aggregate order-rejection events.

    Public methods are thread-safe. ``record`` is on the order-placement
    hot path and intentionally O(1).
    """

    def __init__(
        self,
        routine_log_level: str = "error",
        summary_interval: float = 300.0,
    ) -> None:
        self._level: int = _LEVEL_MAP.get(
            (routine_log_level or "error").lower(), logging.ERROR
        )
        self._summary_interval: float = float(summary_interval)

        # tag -> coin -> _CoinStats. Reset after each summary flush.
        self._stats: Dict[str, Dict[str, _CoinStats]] = defaultdict(
            lambda: defaultdict(_CoinStats)
        )
        self._unknown_count: int = 0
        self._lock = threading.Lock()
        self._last_summary_ts: float = time.monotonic()

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #
    def record(self, coin: str, raw_msg: str) -> str:
        """Record a rejection. Returns the matched tag, or :data:`UNKNOWN_TAG`.

        Emits the per-rejection log at the configured level for routine
        matches; unknown patterns are forwarded to ERROR so format
        changes / new reject reasons remain visible.
        """
        tag = classify_rejection(raw_msg)
        bbo = _extract_bbo(raw_msg)
        now_wall = time.time()

        if tag == UNKNOWN_TAG:
            with self._lock:
                self._unknown_count += 1
            logger.error(f"Order rejected ({coin}): {raw_msg}")
            return tag

        with self._lock:
            stats = self._stats[tag][coin]
            if stats.count == 0:
                stats.first_ts = now_wall
            stats.count += 1
            stats.last_ts = now_wall
            stats.last_bbo = bbo

        # Logger call deliberately outside the lock: the lock guards
        # only the in-memory counters.
        logger.log(self._level, f"[reject:{tag}] {coin} — {raw_msg}")
        return tag

    # ------------------------------------------------------------------ #
    # Periodic summary
    # ------------------------------------------------------------------ #
    def log_summary_if_due(self, now_monotonic: Optional[float] = None) -> bool:
        """Emit a summary line if the configured interval has elapsed.

        Returns ``True`` iff a summary was emitted.
        ``summary_interval <= 0`` disables the summary entirely.
        """
        if self._summary_interval <= 0:
            return False
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        if now - self._last_summary_ts < self._summary_interval:
            return False

        with self._lock:
            snapshot = self._stats
            unknown = self._unknown_count
            self._stats = defaultdict(lambda: defaultdict(_CoinStats))
            self._unknown_count = 0
            self._last_summary_ts = now

        self._emit_summary(snapshot, unknown)
        return True

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _emit_summary(
        self,
        snapshot: Dict[str, Dict[str, _CoinStats]],
        unknown: int,
    ) -> None:
        any_emitted = False
        for tag, by_coin in snapshot.items():
            if not by_coin:
                continue
            total = sum(s.count for s in by_coin.values())
            ranked = sorted(
                by_coin.items(), key=lambda kv: kv[1].count, reverse=True
            )
            top = ", ".join(f"{coin}={s.count}" for coin, s in ranked[:8])
            sample_bbo = ranked[0][1].last_bbo if ranked else ""
            logger.info(
                f"[reject-summary] tag={tag} window={self._summary_interval:.0f}s "
                f"total={total} coins={len(by_coin)} top=[{top}] "
                f"sample_bbo={sample_bbo}"
            )
            any_emitted = True
        if unknown > 0:
            logger.warning(
                f"[reject-summary] tag=unknown window={self._summary_interval:.0f}s "
                f"count={unknown} (unknown patterns logged at ERROR individually)"
            )
            any_emitted = True
        # No-op when both snapshot and unknown are empty: keeps the log
        # quiet during idle periods rather than emitting a meaningless
        # "total=0" line every interval.
        return None if any_emitted else None

    # ------------------------------------------------------------------ #
    # Operational helpers (read-only)
    # ------------------------------------------------------------------ #
    def get_stats_snapshot(self) -> Dict[str, Dict[str, int]]:
        """Read-only counter snapshot for tests / external observers.

        Does not reset internal state (use :meth:`log_summary_if_due`
        for flush semantics).
        """
        with self._lock:
            return {
                tag: {coin: stats.count for coin, stats in by_coin.items()}
                for tag, by_coin in self._stats.items()
            }

    def get_unknown_count(self) -> int:
        with self._lock:
            return self._unknown_count
