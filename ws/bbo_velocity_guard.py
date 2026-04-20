"""BBO velocity guard — cancel orders when BBO moves consistently in one direction.

Unlike BboGuard (which fires on absolute change), VelocityGuard tracks
the *direction* and *consistency* of BBO changes over a sliding window.
When BBO has moved consistently in one direction for N consecutive updates,
cancel the side that is becoming adversely selected.

Example: 3 consecutive bid drops → sell pressure → cancel BUY orders

Usage::

    guard = BboVelocityGuard(order_tracker, consecutive_threshold=3)
    market_data_feed.add_listener(guard.on_l2_update)
    ...
    guard.stop()
"""

import logging
import threading
import time
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


class BboVelocityGuard:
    """Cancel one-sided orders when BBO moves consistently in one direction."""

    def __init__(
        self,
        order_tracker: Any,
        consecutive_threshold: int = 3,
        min_total_move_bps: float = 1.0,
        min_cancel_interval: float = 2.0,
    ) -> None:
        self.order_tracker = order_tracker
        self.consecutive_threshold = consecutive_threshold
        self.min_total_move_bps = min_total_move_bps
        self.min_cancel_interval = min_cancel_interval

        self._prev_bbo: Dict[str, Tuple[float, float]] = {}
        self._consecutive_moves: Dict[str, int] = {}  # positive=up, negative=down
        self._cumulative_bps: Dict[str, float] = {}
        self._last_cancel_time: Dict[str, float] = {}
        self._lock = threading.Lock()

        # Counters
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
                return

            prev_bid, prev_ask = prev
            prev_mid = (prev_bid + prev_ask) / 2
            cur_mid = (bid + ask) / 2
            mid_change_bps = (cur_mid - prev_mid) / prev_mid * 10_000 if prev_mid > 0 else 0.0

            if abs(mid_change_bps) < 0.01:
                return  # No meaningful change

            current_count = self._consecutive_moves.get(coin, 0)
            current_cum = self._cumulative_bps.get(coin, 0.0)

            if mid_change_bps > 0:
                if current_count > 0:
                    self._consecutive_moves[coin] = current_count + 1
                    self._cumulative_bps[coin] = current_cum + mid_change_bps
                else:
                    self._consecutive_moves[coin] = 1
                    self._cumulative_bps[coin] = mid_change_bps
            else:
                if current_count < 0:
                    self._consecutive_moves[coin] = current_count - 1
                    self._cumulative_bps[coin] = current_cum + mid_change_bps
                else:
                    self._consecutive_moves[coin] = -1
                    self._cumulative_bps[coin] = mid_change_bps

            count = self._consecutive_moves[coin]
            cum_bps = self._cumulative_bps[coin]

            if (abs(count) >= self.consecutive_threshold
                    and abs(cum_bps) >= self.min_total_move_bps):
                self._try_directional_cancel(coin, count, cum_bps)
                self._consecutive_moves[coin] = 0
                self._cumulative_bps[coin] = 0.0

        except Exception as e:
            self._error_count += 1
            if self._error_count <= 5 or self._error_count % 100 == 0:
                logger.error("[velocity-guard] Error: %s", e)

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _try_directional_cancel(self, coin: str, count: int, cum_bps: float) -> None:
        """Cancel the adversely-selected side if not rate-limited."""
        now = time.monotonic()
        last = self._last_cancel_time.get(coin)
        if last is not None and now - last < self.min_cancel_interval:
            return

        self._last_cancel_time[coin] = now

        if count > 0:
            # Price moving up → SELL orders at risk of being swept
            self.order_tracker.cancel_orders_by_side(coin, "sell")
            logger.info(
                "[velocity-guard] %s moved up %.1f bps (%d ticks) — cancelled SELL",
                coin, cum_bps, count,
            )
        else:
            # Price moving down → BUY orders at risk
            self.order_tracker.cancel_orders_by_side(coin, "buy")
            logger.info(
                "[velocity-guard] %s moved down %.1f bps (%d ticks) — cancelled BUY",
                coin, abs(cum_bps), abs(count),
            )
        self._cancels_triggered += 1

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def stop(self) -> None:
        """Stop the guard and log summary."""
        self._running = False
        logger.info(
            "[velocity-guard] Stopped (cancels=%d, errors=%d)",
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
            "cancels_triggered": self._cancels_triggered,
            "errors": self._error_count,
        }
