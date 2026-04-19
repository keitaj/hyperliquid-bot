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
            imbalance = self._compute_imbalance(levels)
            if imbalance is None:
                return

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

    def stop(self) -> None:
        """Stop the guard and log summary."""
        self._running = False
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
            "errors": self._error_count,
        }
