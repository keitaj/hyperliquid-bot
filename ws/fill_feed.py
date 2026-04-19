"""WebSocket fill feed for instant opposite-side order cancellation.

Subscribes to ``userFills`` and cancels all tracked orders for a coin
the moment a fill is detected — typically 100-500ms after the fill,
compared to up to 10s with the polling loop.

This dramatically reduces double-fill risk (where both buy and sell
orders fill in the same cycle, doubling adverse selection cost).

Usage::

    feed = FillFeed(info, order_tracker, account_address)
    feed.start()
    ...
    feed.stop()
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class FillFeed:
    """Instant fill detection via WebSocket userFills subscription."""

    def __init__(
        self,
        info: Any,
        order_tracker: Any,
        account_address: str,
    ) -> None:
        self.info = info
        self.order_tracker = order_tracker
        self.account_address = account_address

        self._subscription_id: int = -1
        self._running = False
        self._fill_count = 0
        self._cancel_count = 0
        self._error_count = 0

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Subscribe to userFills. Non-blocking."""
        if self.info.ws_manager is None:
            logger.warning("[ws-fill] WebSocket manager not available, fill feed disabled")
            return

        self._running = True
        try:
            self._subscription_id = self.info.subscribe(
                {"type": "userFills", "user": self.account_address},
                self._on_fill,
            )
            logger.info("[ws-fill] FillFeed started for %s", self.account_address)
        except Exception as e:
            self._error_count += 1
            self._running = False
            logger.error("[ws-fill] Failed to subscribe userFills: %s", e)

    def stop(self) -> None:
        """Unsubscribe and stop the feed."""
        self._running = False
        if self._subscription_id >= 0:
            try:
                self.info.unsubscribe(
                    {"type": "userFills", "user": self.account_address},
                    self._subscription_id,
                )
            except Exception:
                pass
            self._subscription_id = -1
        logger.info(
            "[ws-fill] FillFeed stopped (fills=%d, cancels=%d, errors=%d)",
            self._fill_count,
            self._cancel_count,
            self._error_count,
        )

    # ------------------------------------------------------------------ #
    #  Callback
    # ------------------------------------------------------------------ #

    def _on_fill(self, msg: Dict) -> None:
        """Handle userFills WebSocket message.

        Message format::

            {
                "channel": "userFills",
                "data": {
                    "user": "0x...",
                    "isSnapshot": bool,
                    "fills": [
                        {"coin": "BTC", "px": "...", "sz": "...", "side": "A", ...},
                        ...
                    ]
                }
            }

        On snapshot (initial state), we skip cancellation since these are
        historical fills.  On live fills, we cancel opposite-side orders
        for each affected coin.
        """
        if not self._running:
            return
        try:
            data = msg.get("data", {})

            # Skip snapshot (historical fills sent on subscribe)
            if data.get("isSnapshot", False):
                return

            fills = data.get("fills", [])
            if not fills:
                return

            # Collect unique coins from this batch of fills
            filled_coins: set = set()
            for fill in fills:
                coin = fill.get("coin", "")
                if coin:
                    filled_coins.add(coin)
                    self._fill_count += 1

            # Cancel opposite-side orders for each filled coin.
            # OrderTracker.cancel_all_orders_for_coin is thread-safe (has its own lock).
            for coin in filled_coins:
                self.order_tracker.cancel_all_orders_for_coin(coin)
                self._cancel_count += 1
                logger.info(
                    "[ws-fill] Instant cancel for %s (fill detected via WS)", coin
                )

        except Exception as e:
            self._error_count += 1
            if self._error_count <= 5 or self._error_count % 100 == 0:
                logger.error("[ws-fill] Error processing fill: %s", e)

    # ------------------------------------------------------------------ #
    #  Observability
    # ------------------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._running and self._subscription_id >= 0

    @property
    def stats(self) -> Dict:
        return {
            "running": self._running,
            "fills": self._fill_count,
            "cancels": self._cancel_count,
            "errors": self._error_count,
        }
