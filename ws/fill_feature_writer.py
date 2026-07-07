"""Fill feature JSONL writer — ML training data foundation.

Buffers per-fill feature records built by
:class:`ws.adverse_selection_tracker.AdverseSelectionTracker` and appends
them as JSON lines to a daily-rotated file
(``{log_dir}/YYYYMMDD.jsonl``, UTC date).

Threading model: the WebSocket thread only performs an O(1) in-memory
append via :meth:`add`; all file IO happens on the main loop thread via
:meth:`maybe_flush` (and :meth:`flush_all` on shutdown).

Records are held until "mature" (:data:`MATURITY_SECONDS` after the fill)
so the tracker's 5s/30s/60s markout samples can be embedded in the same
line as training labels (``mo_5s`` / ``mo_30s`` / ``mo_60s``).

All failures are swallowed (fail-silent): feature logging must never
affect trading. This is an observation-only module.
"""

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

# Seconds after fill before a record is flushed. Must exceed the last
# tracker sample interval (60s) so mo_* labels are populated.
MATURITY_SECONDS = 65.0

# Tracker sample label -> JSONL field name.
_SAMPLE_FIELDS = {"5s": "mo_5s", "30s": "mo_30s", "60s": "mo_60s"}


class FillFeatureWriter:
    """Buffered, fail-silent JSONL writer for fill feature records."""

    def __init__(
        self,
        log_dir: str,
        flush_interval: float = 60.0,
        max_daily_bytes: int = 50_000_000,
        buffer_max: int = 5000,
    ) -> None:
        self.log_dir = log_dir
        self.flush_interval = flush_interval
        self.max_daily_bytes = max_daily_bytes

        self._buffer: Deque[Any] = deque(maxlen=buffer_max)
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self._enabled = True

        # Observability counters
        self._written_count = 0
        self._error_count = 0
        self._dropped_overflow = 0
        self._dropped_size_cap = 0
        self._size_cap_warned_date: Optional[str] = None

        try:
            os.makedirs(self.log_dir, exist_ok=True)
        except OSError as e:
            self._enabled = False
            logger.warning(
                f"[fill-features] Cannot create log dir {self.log_dir}: {e} — "
                f"feature logging disabled"
            )

    # ------------------------------------------------------------------ #
    #  WS-thread side (no IO)
    # ------------------------------------------------------------------ #

    def add(self, snapshot: Any) -> None:
        """Buffer a fill snapshot for later flush.

        Called from the WebSocket thread — must stay O(1) and never
        perform IO. ``snapshot`` is a ``FillSnapshot`` whose ``record``
        dict has been populated by the tracker.
        """
        if not self._enabled:
            return
        try:
            with self._lock:
                if (
                    self._buffer.maxlen is not None
                    and len(self._buffer) == self._buffer.maxlen
                ):
                    # deque(maxlen) evicts the oldest entry on append.
                    self._dropped_overflow += 1
                self._buffer.append(snapshot)
        except Exception as e:
            self._record_error(e)

    # ------------------------------------------------------------------ #
    #  Main-loop side (IO)
    # ------------------------------------------------------------------ #

    def maybe_flush(self) -> None:
        """Flush mature records if the flush interval has elapsed.

        Should be called from the main loop. Never raises.
        """
        if not self._enabled:
            return
        now = time.monotonic()
        if now - self._last_flush < self.flush_interval:
            return
        self._last_flush = now
        self._flush(force=False)

    def flush_all(self, force: bool = True) -> None:
        """Flush all buffered records, including immature ones.

        Called on shutdown so no records are lost; immature records are
        written with ``null`` markout labels. Never raises.
        """
        if not self._enabled:
            return
        self._flush(force=force)

    def _flush(self, force: bool) -> None:
        try:
            now = time.monotonic()
            mature: List[Any] = []
            with self._lock:
                # Buffer is appended chronologically, so we can stop at
                # the first immature record.
                while self._buffer:
                    snap = self._buffer[0]
                    if not force and now - snap.fill_time < MATURITY_SECONDS:
                        break
                    mature.append(self._buffer.popleft())

            if not mature:
                return

            lines = []
            for snap in mature:
                record = dict(snap.record or {})
                for label, field in _SAMPLE_FIELDS.items():
                    record[field] = snap.samples.get(label)
                lines.append(json.dumps(record, separators=(",", ":")))

            path = self._current_path()
            if self._over_size_cap(path):
                self._dropped_size_cap += len(lines)
                return

            with open(path, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
            self._written_count += len(lines)

        except Exception as e:
            self._record_error(e)

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _current_path(self) -> str:
        """Daily-rotated file path based on the current UTC date."""
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        return os.path.join(self.log_dir, f"{day}.jsonl")

    def _over_size_cap(self, path: str) -> bool:
        """Check the daily size cap; warn at most once per UTC date."""
        try:
            if os.path.getsize(path) <= self.max_daily_bytes:
                return False
        except OSError:
            return False  # file does not exist yet
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        if self._size_cap_warned_date != day:
            self._size_cap_warned_date = day
            logger.warning(
                f"[fill-features] Daily size cap ({self.max_daily_bytes} bytes) "
                f"reached for {path} — dropping records for the rest of the day"
            )
        return True

    def _record_error(self, e: Exception) -> None:
        self._error_count += 1
        if self._error_count <= 5 or self._error_count % 100 == 0:
            logger.error(f"[fill-features] Error (count={self._error_count}): {e}")

    # ------------------------------------------------------------------ #
    #  Observability
    # ------------------------------------------------------------------ #

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            buffered = len(self._buffer)
        return {
            "enabled": self._enabled,
            "buffered": buffered,
            "written": self._written_count,
            "errors": self._error_count,
            "dropped_overflow": self._dropped_overflow,
            "dropped_size_cap": self._dropped_size_cap,
        }
