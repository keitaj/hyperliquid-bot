"""BBO change detection guard for stale quote cancellation.

Monitors real-time l2Book updates via :meth:`MarketDataFeed.add_listener`
callback.  When BBO moves beyond a configurable threshold, immediately
cancels all tracked orders for the affected coin — reducing the window
from ``REFRESH_INTERVAL`` (typically 60 s) down to WS latency (~500 ms).

Usage::

    guard = BboGuard(order_tracker, threshold_bps=2.0)
    market_data_feed.add_listener(guard.on_l2_update)
    ...
    guard.stop()
"""

import logging
import threading
import time
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


class BboGuard:
    """Cancel stale quotes when BBO changes significantly."""

    def __init__(
        self,
        order_tracker: Any,
        threshold_bps: float = 2.0,
        min_cancel_interval: float = 1.0,
    ) -> None:
        self.order_tracker = order_tracker
        self.threshold_bps = threshold_bps
        self.min_cancel_interval = min_cancel_interval

        self._prev_bbo: Dict[str, Tuple[float, float]] = {}  # coin -> (bid, ask)
        self._last_cancel_time: Dict[str, float] = {}
        self._lock = threading.Lock()

        # Counters
        self._changes_detected = 0
        self._cancels_triggered = 0
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
            bid, ask = self._extract_bbo(levels)
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
                self._changes_detected += 1
                self._try_cancel(coin, max_change)

        except Exception as e:
            self._error_count += 1
            if self._error_count <= 5 or self._error_count % 100 == 0:
                logger.error("[bbo-guard] Error: %s", e)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_bbo(levels: Any) -> Tuple[float, float]:
        """Extract best bid and ask from l2Book levels."""
        bids = levels[0] if len(levels) > 0 else []
        asks = levels[1] if len(levels) > 1 else []
        bid = float(bids[0]["px"]) if bids else 0.0
        ask = float(asks[0]["px"]) if asks else 0.0
        return bid, ask

    def _try_cancel(self, coin: str, change_bps: float) -> None:
        """Cancel orders if not rate-limited."""
        now = time.monotonic()
        last = self._last_cancel_time.get(coin, 0)
        if now - last < self.min_cancel_interval:
            return  # Rate limit: avoid spamming cancels

        self._last_cancel_time[coin] = now
        self.order_tracker.cancel_all_orders_for_coin(coin)
        self._cancels_triggered += 1
        logger.info(
            "[bbo-guard] BBO change %.1f bps for %s — cancelled orders",
            change_bps,
            coin,
        )

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def stop(self) -> None:
        """Stop the guard and log summary."""
        self._running = False
        logger.info(
            "[bbo-guard] Stopped (changes=%d, cancels=%d, errors=%d)",
            self._changes_detected,
            self._cancels_triggered,
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
            "changes_detected": self._changes_detected,
            "cancels_triggered": self._cancels_triggered,
            "errors": self._error_count,
        }
