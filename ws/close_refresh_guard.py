"""Close order refresh guard for taker fill prevention.

Monitors l2Book updates via :meth:`MarketDataFeed.add_listener` callback.
When BBO changes significantly, invalidates the current close order in
:class:`PositionCloser` so it gets re-placed at the updated BBO-aligned
price on the next ``manage()`` cycle.

This complements :class:`BboGuard` (which handles entry orders) by
ensuring close orders also track BBO movements, reducing force taker
closes.

Usage::

    guard = CloseRefreshGuard(position_closer, threshold_bps=1.0)
    market_data_feed.add_listener(guard.on_l2_update)
    ...
    guard.stop()
"""

import logging
import threading
import time
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


class CloseRefreshGuard:
    """Refresh close orders when BBO changes to improve maker close fill rate."""

    def __init__(
        self,
        position_closer: Any,
        threshold_bps: float = 1.0,
        min_refresh_interval: float = 3.0,
    ) -> None:
        self.position_closer = position_closer
        self.threshold_bps = threshold_bps
        self.min_refresh_interval = min_refresh_interval

        self._prev_bbo: Dict[str, Tuple[float, float]] = {}  # coin -> (bid, ask)
        self._last_refresh_time: Dict[str, float] = {}
        self._lock = threading.Lock()

        # Counters
        self._refreshes_triggered = 0
        self._skipped_no_order = 0
        self._error_count = 0
        self._running = True

    # ------------------------------------------------------------------ #
    #  Callback
    # ------------------------------------------------------------------ #

    def on_l2_update(self, coin: str, levels: Any) -> None:
        """Callback from MarketDataFeed.  Runs on the WS thread."""
        if not self._running:
            return
        try:
            bids = levels[0] if len(levels) > 0 else []
            asks = levels[1] if len(levels) > 1 else []
            bid = float(bids[0]["px"]) if bids else 0.0
            ask = float(asks[0]["px"]) if asks else 0.0
            if bid <= 0 or ask <= 0:
                return

            with self._lock:
                prev = self._prev_bbo.get(coin)
                self._prev_bbo[coin] = (bid, ask)

            if prev is None:
                return  # First update — establish baseline

            prev_bid, prev_ask = prev
            bid_change_bps = abs(bid - prev_bid) / prev_bid * 10_000
            ask_change_bps = abs(ask - prev_ask) / prev_ask * 10_000
            max_change = max(bid_change_bps, ask_change_bps)

            if max_change >= self.threshold_bps:
                self._try_refresh(coin, max_change)

        except Exception as e:
            self._error_count += 1
            if self._error_count <= 5 or self._error_count % 100 == 0:
                logger.error("[close-refresh] Error: %s", e)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _try_refresh(self, coin: str, change_bps: float) -> None:
        """Invalidate close order if not rate-limited."""
        now = time.monotonic()
        last = self._last_refresh_time.get(coin)
        if last is not None and now - last < self.min_refresh_interval:
            return

        self._last_refresh_time[coin] = now
        refreshed = self.position_closer.invalidate_close_order(coin)
        if refreshed:
            self._refreshes_triggered += 1
            logger.info(
                "[close-refresh] BBO change %.1f bps for %s — refreshed close order",
                change_bps, coin,
            )
        else:
            self._skipped_no_order += 1

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def stop(self) -> None:
        """Stop the guard and log summary."""
        self._running = False
        logger.info(
            "[close-refresh] Stopped (refreshes=%d, skipped=%d, errors=%d)",
            self._refreshes_triggered,
            self._skipped_no_order,
            self._error_count,
        )

    # ------------------------------------------------------------------ #
    #  Observability
    # ------------------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> Dict:
        return {
            "running": self._running,
            "refreshes_triggered": self._refreshes_triggered,
            "skipped_no_order": self._skipped_no_order,
            "errors": self._error_count,
        }
