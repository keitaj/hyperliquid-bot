"""Order tracking and stale order management for market-making strategy."""

import logging
import time
from typing import Dict, List, Optional, Set, Tuple

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

    def get_order_count(self, coin: str) -> int:
        return len(self._tracked_orders.get(coin, []))

    def record_order(self, coin: str, oid: int, side: str) -> None:
        now = time.time()
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
        for coin, orders in self._tracked_orders.items():
            if orders:
                active.add(coin)
        return len(active)

    def cancel_stale_orders(self, coin: str, close_oid: Optional[int] = None) -> None:
        """Cancel orders older than refresh_interval and remove filled/cancelled from tracking."""
        tracked = self._tracked_orders.get(coin, [])
        if not tracked:
            return

        now = time.time()
        still_active: List[Tuple[int, str, float]] = []

        try:
            open_orders = self.order_manager.get_open_orders(coin)
            open_oids = {int(o['oid']) for o in open_orders}
        except Exception as e:
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

        self._tracked_orders[coin] = still_active
