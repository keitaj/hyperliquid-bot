"""WebSocket feed that keeps MarketDataManager caches up to date.

Subscribes to ``l2Book`` for each trading coin and pushes updates into
:class:`MarketDataManager` via :meth:`update_from_ws`.  The polling loop
continues to run at its normal interval but almost always hits the
warm cache, eliminating REST L2 calls during normal operation.

Usage::

    feed = MarketDataFeed(info, market_data, coins)
    feed.start()   # non-blocking; runs on the SDK's WS thread
    ...
    feed.stop()
"""

import logging
import threading
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class MarketDataFeed:
    """Bridge between Hyperliquid WebSocket and MarketDataManager."""

    def __init__(
        self,
        info: Any,
        market_data: Any,
        coins: List[str],
    ) -> None:
        self.info = info
        self.market_data = market_data
        self.coins = list(coins)

        self._subscription_ids: Dict[str, int] = {}
        self._lock = threading.Lock()
        self._running = False
        self._update_count = 0
        self._error_count = 0
        self._last_update: Dict[str, float] = {}

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Subscribe to l2Book for all coins.  Non-blocking."""
        if self.info.ws_manager is None:
            logger.warning("[ws] WebSocket manager not available (skip_ws?), feed disabled")
            return

        self._running = True
        for coin in self.coins:
            self._subscribe_coin(coin)

        logger.info(
            "[ws] MarketDataFeed started — subscribed to %d coins: %s",
            len(self._subscription_ids),
            ", ".join(sorted(self._subscription_ids.keys())),
        )

    def stop(self) -> None:
        """Unsubscribe all coins and stop the feed."""
        self._running = False
        with self._lock:
            for coin, sub_id in list(self._subscription_ids.items()):
                try:
                    self.info.unsubscribe(
                        {"type": "l2Book", "coin": coin},
                        sub_id,
                    )
                except Exception:
                    pass
            self._subscription_ids.clear()
        logger.info(
            "[ws] MarketDataFeed stopped (updates=%d, errors=%d)",
            self._update_count,
            self._error_count,
        )

    # ------------------------------------------------------------------ #
    #  Subscription management
    # ------------------------------------------------------------------ #

    def _subscribe_coin(self, coin: str) -> None:
        """Subscribe to l2Book for a single coin."""
        try:
            sub_id = self.info.subscribe(
                {"type": "l2Book", "coin": coin},
                self._on_l2_update,
            )
            with self._lock:
                self._subscription_ids[coin] = sub_id
            logger.debug("[ws] Subscribed l2Book for %s (id=%d)", coin, sub_id)
        except Exception as e:
            self._error_count += 1
            logger.error("[ws] Failed to subscribe l2Book for %s: %s", coin, e)

    def _on_l2_update(self, msg: Dict) -> None:
        """Callback invoked on the SDK WebSocket thread."""
        if not self._running:
            return
        try:
            data = msg.get("data", {})
            coin = data.get("coin", "")
            levels = data.get("levels")
            if not coin or not levels:
                return

            self.market_data.update_from_ws(coin, levels)

            self._update_count += 1
            self._last_update[coin] = time.monotonic()

        except Exception as e:
            self._error_count += 1
            if self._error_count <= 5 or self._error_count % 100 == 0:
                logger.error("[ws] Error processing l2Book update: %s", e)

    # ------------------------------------------------------------------ #
    #  Observability
    # ------------------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._running and bool(self._subscription_ids)

    @property
    def stats(self) -> Dict:
        return {
            "running": self._running,
            "subscriptions": len(self._subscription_ids),
            "updates": self._update_count,
            "errors": self._error_count,
        }

    def stale_coins(self, max_age: float = 30.0) -> List[str]:
        """Return coins that haven't received a WS update in *max_age* seconds."""
        now = time.monotonic()
        return [
            c for c in self.coins
            if now - self._last_update.get(c, 0) > max_age
        ]
