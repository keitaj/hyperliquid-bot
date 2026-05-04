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
        self._adverse_tracker: Any = None
        self._position_closer: Any = None
        self._coin_health_tracker: Any = None

    def set_adverse_selection_tracker(self, tracker: Any) -> None:
        """Register an adverse selection tracker to receive fill notifications."""
        self._adverse_tracker = tracker

    def set_position_closer(self, closer: Any) -> None:
        """Register PositionCloser for close-fill cleanup.

        When a fill is detected, ``on_position_closed(coin)`` is called
        to clear stale tracking state — preventing reduce-only rejections
        from race conditions between the WS thread and main loop.
        """
        self._position_closer = closer

    def set_coin_health_tracker(self, tracker: Any) -> None:
        """Register a CoinHealthTracker (Forager) to receive fill events.

        Activity is recorded on every fill; closes (``closedPnl != 0``)
        also feed the quality / cost dimensions.
        """
        self._coin_health_tracker = tracker

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

            # Notify adverse selection tracker (observation only, no trading impact)
            if self._adverse_tracker is not None:
                for fill in fills:
                    try:
                        coin = fill.get("coin", "")
                        px = float(fill.get("px", 0))
                        side = fill.get("side", "")
                        fill_time = fill.get("time")
                        if coin and px > 0 and side:
                            self._adverse_tracker.on_fill(coin, px, side, fill_time)
                    except Exception as e:
                        logger.debug("[ws-fill] Error notifying adverse tracker: %s", e)

            # Notify Forager's CoinHealthTracker (observation only).
            # Every fill updates activity; closes (closedPnl != 0) feed
            # the quality + cost dimensions.
            if self._coin_health_tracker is not None:
                for fill in fills:
                    try:
                        coin = fill.get("coin", "")
                        if not coin:
                            continue
                        self._coin_health_tracker.record_fill(coin)
                        closed_pnl_raw = fill.get("closedPnl")
                        if closed_pnl_raw is None or float(closed_pnl_raw) == 0.0:
                            continue
                        # Treat fills with non-zero closedPnl as position closes.
                        px = float(fill.get("px", 0))
                        sz = float(fill.get("sz", 0))
                        crossed = bool(fill.get("crossed", False))
                        fee = float(fill.get("fee", 0))
                        if px <= 0 or sz <= 0:
                            continue
                        notional = sz * px
                        net_pnl = float(closed_pnl_raw) - fee
                        self._coin_health_tracker.record_close(
                            coin=coin,
                            is_maker=(not crossed),
                            net_pnl=net_pnl,
                            notional=notional,
                        )
                    except Exception as e:
                        logger.debug(
                            "[ws-fill] Error notifying coin health tracker: %s", e
                        )

            # Cancel opposite-side orders for each filled coin.
            # OrderTracker.cancel_all_orders_for_coin is thread-safe (has its own lock).
            for coin in filled_coins:
                self.order_tracker.cancel_all_orders_for_coin(coin, reason="ws_fill")
                self._cancel_count += 1
                logger.info(
                    "[ws-fill] Instant cancel for %s (fill detected via WS)", coin
                )

            # Notify PositionCloser that positions may have closed.
            # This clears stale tracking state to prevent reduce-only
            # rejections when the close order itself was the fill.
            if self._position_closer is not None:
                for coin in filled_coins:
                    self._position_closer.on_position_closed(coin)

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
