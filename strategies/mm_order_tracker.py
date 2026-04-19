"""Order tracking and stale order management for market-making strategy."""

import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from rate_limiter import API_ERRORS

logger = logging.getLogger(__name__)


class OrderTracker:
    """Tracks live market-making orders per coin, cancels stale ones."""

    def __init__(self, order_manager, refresh_interval_seconds: float, max_open_orders: int) -> None:
        self.order_manager = order_manager
        self.refresh_interval_seconds = refresh_interval_seconds
        self.max_open_orders = max_open_orders

        # coin -> list of (oid, side, place_time)
        self._tracked_orders: Dict[str, List[Tuple[int, str, float]]] = {}
        self._last_order_time: Dict[str, float] = {}
        self._lock = threading.Lock()

    def get_order_count(self, coin: str) -> int:
        with self._lock:
            return len(self._tracked_orders.get(coin, []))

    def record_order(self, coin: str, oid: int, side: str) -> None:
        now = time.monotonic()
        with self._lock:
            if coin not in self._tracked_orders:
                self._tracked_orders[coin] = []
            self._tracked_orders[coin].append((oid, side, now))
            self._last_order_time[coin] = now

    def active_coins(self, positions: Dict, open_positions_keys: Set[str]) -> int:
        """Count coins with positions, pending close orders, or tracked orders."""
        active: Set[str] = set()
        for coin, pos in positions.items():
            if abs(pos.get('size', 0)) > 0:
                active.add(coin)
        active.update(open_positions_keys)
        with self._lock:
            for coin, orders in self._tracked_orders.items():
                if orders:
                    active.add(coin)
        return len(active)

    def cancel_stale_orders(self, coin: str, close_oid: Optional[int] = None) -> None:
        """Cancel orders older than refresh_interval and remove filled/cancelled from tracking."""
        with self._lock:
            tracked = list(self._tracked_orders.get(coin, []))
        if not tracked:
            return

        now = time.monotonic()
        still_active: List[Tuple[int, str, float]] = []

        try:
            open_orders = self.order_manager.get_open_orders(coin)
            open_oids = {int(o['oid']) for o in open_orders}
        except API_ERRORS as e:
            logger.error(f"[mm] Error fetching open orders for {coin}: {e}")
            return

        to_cancel: List[Tuple[int, str]] = []  # (oid, side) pairs to bulk cancel

        for oid, side, place_time in tracked:
            if oid not in open_oids:
                logger.debug(f"[mm] Order {oid} ({side} {coin}) no longer open")
                continue

            # Never cancel a close order from here
            if close_oid is not None and oid == close_oid:
                still_active.append((oid, side, place_time))
                continue

            age = now - place_time
            if age >= self.refresh_interval_seconds:
                to_cancel.append((oid, side))
            else:
                still_active.append((oid, side, place_time))

        if to_cancel:
            cancel_requests = [{"coin": coin, "oid": oid} for oid, _ in to_cancel]
            cancelled = self.order_manager.bulk_cancel_orders(cancel_requests)
            for oid, side in to_cancel:
                logger.info(f"[mm] Cancelled stale {side} order {oid} for {coin}")
            if cancelled < len(to_cancel):
                logger.warning(
                    f"[mm] Bulk cancel: {cancelled}/{len(to_cancel)} succeeded for {coin}"
                )

        with self._lock:
            self._tracked_orders[coin] = still_active

    def cancel_all_orders_for_coin(self, coin: str) -> None:
        """Cancel all tracked orders for a coin.

        Called when one side fills to prevent the opposite side from also
        filling (which would double adverse selection cost).

        Thread-safe: may be called from the WS fill feed thread.
        """
        with self._lock:
            tracked = list(self._tracked_orders.get(coin, []))
            self._tracked_orders[coin] = []
        if not tracked:
            return

        cancel_requests = [{"coin": coin, "oid": oid} for oid, _side, _t in tracked]
        oid_list = [oid for oid, _, _ in tracked]
        try:
            cancelled = self.order_manager.bulk_cancel_orders(cancel_requests)
            if cancelled >= len(cancel_requests):
                logger.info(
                    f"[mm] Cancelled {cancelled} orders for {coin} (post-fill cleanup): {oid_list}"
                )
            else:
                logger.warning(
                    f"[mm] Post-fill cancel: {cancelled}/{len(cancel_requests)} "
                    f"succeeded for {coin}, attempted: {oid_list}"
                )
        except Exception as e:
            logger.error(
                f"[mm] Error cancelling orders for {coin} after fill: {e}, oids: {oid_list}"
            )

    def cancel_orders_by_side(self, coin: str, side: str) -> None:
        """Cancel tracked orders for a specific side of a coin.

        Only cancels orders matching *side* (``"B"`` for buy, ``"A"`` for sell),
        leaving the opposite side intact.

        Thread-safe: may be called from the WS imbalance guard thread.
        """
        with self._lock:
            tracked = self._tracked_orders.get(coin, [])
            to_cancel = [(oid, s, t) for oid, s, t in tracked if s == side]
            remaining = [(oid, s, t) for oid, s, t in tracked if s != side]
            self._tracked_orders[coin] = remaining

        if not to_cancel:
            return

        cancel_requests = [{"coin": coin, "oid": oid} for oid, _, _ in to_cancel]
        oid_list = [oid for oid, _, _ in to_cancel]
        try:
            cancelled = self.order_manager.bulk_cancel_orders(cancel_requests)
            logger.info(
                f"[mm] Cancelled {cancelled} {side} order(s) for {coin} "
                f"(imbalance guard): {oid_list}"
            )
        except Exception as e:
            logger.error(
                f"[mm] Error cancelling {side} orders for {coin}: {e}, oids: {oid_list}"
            )
