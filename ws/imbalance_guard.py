"""L2 book imbalance guard for adverse selection prevention.

Monitors real-time l2Book updates via :meth:`MarketDataFeed.add_listener`
callback.  When book imbalance crosses a threshold, cancels orders on
the side likely to be adversely selected:

- imbalance < -threshold (ask-heavy / sell pressure) → cancel BUY orders
- imbalance > +threshold (bid-heavy / buy pressure) → cancel SELL orders

Complements the existing ``imbalance_threshold`` in MarketMakingStrategy
(which prevents placement) by also cancelling already-resting orders.

Uses a **state-transition** model: cancellation fires only on
neutral → risky transitions, avoiding repeated API calls while the
state persists.

Usage::

    guard = ImbalanceGuard(order_tracker, threshold=0.5, depth=5)
    market_data_feed.add_listener(guard.on_l2_update)
    ...
    guard.stop()
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# State constants
_NEUTRAL = "neutral"
_BUY_RISKY = "buy_risky"    # ask-heavy → buying is risky
_SELL_RISKY = "sell_risky"   # bid-heavy → selling is risky


class ImbalanceGuard:
    """Cancel one-sided orders when L2 book becomes heavily skewed."""

    def __init__(
        self,
        order_tracker: Any,
        threshold: float = 0.5,
        depth: int = 5,
        min_cancel_interval: float = 2.0,
    ) -> None:
        self.order_tracker = order_tracker
        self.threshold = threshold
        self.depth = depth
        self.min_cancel_interval = min_cancel_interval

        self._prev_state: Dict[str, str] = {}  # coin -> state
        self._last_cancel_time: Dict[str, float] = {}
        self._lock = threading.Lock()

        # Counters
        self._changes_detected = 0
        self._cancels_triggered = 0
        self._skipped_rate_limit = 0
        self._error_count = 0
        self._update_count = 0
        self._running = True

        # Per-coin max absolute imbalance seen (for diagnostics)
        self._max_imbalance: Dict[str, float] = {}
        self._summary_interval = 300.0  # seconds
        self._last_summary_time = time.monotonic()

    # ------------------------------------------------------------------ #
    #  Callback
    # ------------------------------------------------------------------ #

    def on_l2_update(self, coin: str, levels: Any) -> None:
        """Callback from MarketDataFeed.  Runs on the WS thread."""
        if not self._running:
            return
        try:
            imbalance = self._compute_imbalance(levels)
            if imbalance is None:
                return

            self._update_count += 1
            abs_imb = abs(imbalance)
            prev_max = self._max_imbalance.get(coin, 0.0)
            if abs_imb > prev_max:
                self._max_imbalance[coin] = abs_imb

            # Determine current state
            if imbalance < -self.threshold:
                new_state = _BUY_RISKY
            elif imbalance > self.threshold:
                new_state = _SELL_RISKY
            else:
                new_state = _NEUTRAL

            with self._lock:
                prev_state = self._prev_state.get(coin, _NEUTRAL)
                self._prev_state[coin] = new_state

            # Only act on state transitions into a risky state
            if new_state != prev_state and new_state != _NEUTRAL:
                self._changes_detected += 1
                side = "B" if new_state == _BUY_RISKY else "A"
                self._try_cancel(coin, side, imbalance)

        except Exception as e:
            self._error_count += 1
            if self._error_count <= 5 or self._error_count % 100 == 0:
                logger.error("[imb-guard] Error: %s", e)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _compute_imbalance(self, levels: Any) -> Optional[float]:
        """Compute book imbalance from raw l2Book levels.

        Same formula as ``MarketDataManager._parse_levels()``:
        ``(bid_size - ask_size) / (bid_size + ask_size)``
        """
        if len(levels) < 2:
            return None
        bids = levels[0]
        asks = levels[1]
        if not bids or not asks:
            return None

        depth = min(self.depth, len(bids), len(asks))
        bid_size = sum(float(bids[i]["sz"]) for i in range(depth))
        ask_size = sum(float(asks[i]["sz"]) for i in range(depth))
        total = bid_size + ask_size
        if total <= 0:
            return None
        return (bid_size - ask_size) / total

    def _try_cancel(self, coin: str, side: str, imbalance: float) -> None:
        """Cancel orders on *side* if not rate-limited."""
        now = time.monotonic()
        key = f"{coin}:{side}"
        last = self._last_cancel_time.get(key, 0)
        if now - last < self.min_cancel_interval:
            self._skipped_rate_limit += 1
            return

        self._last_cancel_time[key] = now
        self.order_tracker.cancel_orders_by_side(coin, side)
        self._cancels_triggered += 1
        side_label = "BUY" if side == "B" else "SELL"
        logger.info(
            "[imb-guard] Imbalance %.2f for %s — cancelled %s orders",
            imbalance, coin, side_label,
        )

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def maybe_log_summary(self) -> None:
        """Log periodic summary if interval has elapsed.

        Should be called from the main loop (e.g., after strategy.run()).
        """
        now = time.monotonic()
        if now - self._last_summary_time < self._summary_interval:
            return
        self._last_summary_time = now
        self._log_summary()

    def _log_summary(self) -> None:
        """Log summary with max imbalance per coin and reset counters."""
        max_imb = dict(self._max_imbalance)
        updates = self._update_count
        cancels = self._cancels_triggered
        skipped = self._skipped_rate_limit

        # Reset periodic counters
        self._max_imbalance.clear()
        self._update_count = 0
        # Don't reset cumulative cancels/changes — those are lifetime counters

        if not max_imb and updates == 0:
            return

        coin_parts = ", ".join(
            f"{coin}={imb:.2f}" for coin, imb in sorted(max_imb.items(), key=lambda x: -x[1])
        )
        logger.info(
            f"[imb-guard] Summary: updates={updates} cancels={cancels} skipped={skipped} "
            f"threshold={self.threshold:.2f} max_imbalance=[{coin_parts}]"
        )

    def stop(self) -> None:
        """Stop the guard and log summary."""
        self._running = False
        self._log_summary()
        logger.info(
            "[imb-guard] Stopped (changes=%d, cancels=%d, errors=%d)",
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
            "skipped_rate_limit": self._skipped_rate_limit,
            "errors": self._error_count,
            "update_count": self._update_count,
            "max_imbalance": dict(self._max_imbalance),
        }
