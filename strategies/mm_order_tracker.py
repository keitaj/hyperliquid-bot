"""Order tracking and stale order management for market-making strategy."""

import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from rate_limiter import API_ERRORS

logger = logging.getLogger(__name__)

# Per-tracked-order tuple: (oid, side, place_time, price)
# ``price`` is the limit price recorded at placement time; used by
# ``refresh_orders_with_tolerance`` to decide whether the order is still
# close enough to the current ideal price to keep (preserve queue priority).
# Defaults to 0.0 when unspecified (tolerance-based keep is then disabled
# for that order and it falls back to age-based cancellation).
TrackedOrder = Tuple[int, str, float, float]


class OrderTracker:
    """Tracks live market-making orders per coin, cancels stale ones."""

    def __init__(self, order_manager, refresh_interval_seconds: float, max_open_orders: int) -> None:
        self.order_manager = order_manager
        self.refresh_interval_seconds = refresh_interval_seconds
        self.max_open_orders = max_open_orders

        # coin -> list of (oid, side, place_time, price)
        self._tracked_orders: Dict[str, List[TrackedOrder]] = {}
        self._last_order_time: Dict[str, float] = {}
        self._lock = threading.Lock()

        # Counters for observability (cumulative, never reset).
        self._refresh_kept: Dict[str, int] = {}
        self._refresh_cancelled_drift: Dict[str, int] = {}
        self._refresh_cancelled_age: Dict[str, int] = {}

    def get_order_count(self, coin: str) -> int:
        with self._lock:
            return len(self._tracked_orders.get(coin, []))

    def get_open_sides(self, coin: str) -> Set[str]:
        """Return set of sides with currently tracked orders for ``coin``.

        Used by ``_place_orders`` to skip placing a new quote on a side
        that already has a kept order (avoids duplicate same-side orders).
        """
        with self._lock:
            return {entry[1] for entry in self._tracked_orders.get(coin, [])}

    def record_order(self, coin: str, oid: int, side: str, price: float = 0.0) -> None:
        """Record a newly placed order.

        ``price`` is optional for backward compatibility with callers that
        do not yet provide it (tolerance-based keep will be disabled for
        such orders, which fall back to age-only cancellation).
        """
        now = time.monotonic()
        with self._lock:
            if coin not in self._tracked_orders:
                self._tracked_orders[coin] = []
            self._tracked_orders[coin].append((oid, side, now, float(price)))
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
        still_active: List[TrackedOrder] = []

        try:
            open_orders = self.order_manager.get_open_orders(coin)
            open_oids = {int(o['oid']) for o in open_orders}
        except API_ERRORS as e:
            logger.error(f"[mm] Error fetching open orders for {coin}: {e}")
            return

        to_cancel: List[Tuple[int, str]] = []  # (oid, side) pairs to bulk cancel

        for oid, side, place_time, price in tracked:
            if oid not in open_oids:
                logger.debug(f"[mm] Order {oid} ({side} {coin}) no longer open")
                continue

            # Never cancel a close order from here
            if close_oid is not None and oid == close_oid:
                still_active.append((oid, side, place_time, price))
                continue

            age = now - place_time
            if age >= self.refresh_interval_seconds:
                to_cancel.append((oid, side))
            else:
                still_active.append((oid, side, place_time, price))

        if to_cancel:
            cancel_requests = [{"coin": coin, "oid": oid} for oid, _ in to_cancel]
            cancelled = self.order_manager.bulk_cancel_orders(cancel_requests)
            for oid, side in to_cancel:
                logger.info(f"[mm] Cancelled stale {side} order {oid} for {coin}")
            if cancelled < len(to_cancel):
                logger.debug(
                    f"[mm] Bulk cancel: {cancelled}/{len(to_cancel)} succeeded for {coin}"
                )

        with self._lock:
            self._tracked_orders[coin] = still_active

    def refresh_orders_with_tolerance(
        self,
        coin: str,
        ideal_prices: Dict[str, float],
        tolerance_bp: float,
        max_age_seconds: float,
        close_oid: Optional[int] = None,
    ) -> Dict[str, int]:
        """Selectively cancel orders based on price drift and age.

        For each tracked order, an order is **kept** when both:
          * its recorded ``price`` is within ``tolerance_bp`` basis points of
            ``ideal_prices[side]``, and
          * its age is below ``max_age_seconds``.

        Otherwise it is cancelled. This preserves queue priority for orders
        whose price is still fresh, while immediately re-quoting when the
        market has moved beyond tolerance. The ``max_age_seconds`` clamp acts
        as a safety net to ensure no order is kept indefinitely.

        Orders matching ``close_oid`` are always kept (close-side orders are
        owned by ``MMPositionCloser``).

        Orders for which a recorded price is unavailable (``price <= 0``) or
        whose side is missing from ``ideal_prices`` fall back to age-only
        cancellation (using ``self.refresh_interval_seconds``).

        Returns a dict ``{"kept": N, "cancelled_drift": M, "cancelled_age": K}``
        for the current call (cumulative counters are also updated and
        accessible via :meth:`get_refresh_stats`).
        """
        result = {"kept": 0, "cancelled_drift": 0, "cancelled_age": 0}

        with self._lock:
            tracked = list(self._tracked_orders.get(coin, []))
        if not tracked:
            return result

        now = time.monotonic()
        still_active: List[TrackedOrder] = []

        try:
            open_orders = self.order_manager.get_open_orders(coin)
            open_oids = {int(o['oid']) for o in open_orders}
        except API_ERRORS as e:
            logger.error(f"[mm] Error fetching open orders for {coin}: {e}")
            return result

        to_cancel: List[Tuple[int, str, str]] = []  # (oid, side, reason)

        for oid, side, place_time, price in tracked:
            if oid not in open_oids:
                logger.debug(f"[mm] Order {oid} ({side} {coin}) no longer open")
                continue

            # Never touch a close order from here.
            if close_oid is not None and oid == close_oid:
                still_active.append((oid, side, place_time, price))
                continue

            age = now - place_time
            ideal = ideal_prices.get(side)

            # Fallback to age-only when we cannot evaluate drift.
            if ideal is None or ideal <= 0 or price <= 0:
                if age >= self.refresh_interval_seconds:
                    to_cancel.append((oid, side, "age"))
                else:
                    still_active.append((oid, side, place_time, price))
                continue

            drift_bp = abs(price - ideal) / ideal * 10_000.0

            if drift_bp <= tolerance_bp and age < max_age_seconds:
                still_active.append((oid, side, place_time, price))
                result["kept"] += 1
                logger.debug(
                    f"[mm] {coin} keeping {side} oid={oid} drift={drift_bp:.2f}bp "
                    f"<= tol={tolerance_bp}bp age={age:.1f}s"
                )
            elif age >= max_age_seconds and drift_bp <= tolerance_bp:
                # Tolerance ok but exceeded the safety-net age clamp.
                to_cancel.append((oid, side, "age"))
            else:
                to_cancel.append((oid, side, "drift"))

        if to_cancel:
            cancel_requests = [{"coin": coin, "oid": oid} for oid, _, _ in to_cancel]
            cancelled = self.order_manager.bulk_cancel_orders(cancel_requests)
            for oid, side, reason in to_cancel:
                if reason == "drift":
                    result["cancelled_drift"] += 1
                else:
                    result["cancelled_age"] += 1
                logger.info(
                    f"[mm] Cancelled {reason} {side} order {oid} for {coin}"
                )
            if cancelled < len(to_cancel):
                logger.debug(
                    f"[mm] Bulk cancel: {cancelled}/{len(to_cancel)} succeeded for {coin}"
                )

        with self._lock:
            self._tracked_orders[coin] = still_active

        # Update cumulative counters.
        if result["kept"]:
            self._refresh_kept[coin] = self._refresh_kept.get(coin, 0) + result["kept"]
        if result["cancelled_drift"]:
            self._refresh_cancelled_drift[coin] = (
                self._refresh_cancelled_drift.get(coin, 0) + result["cancelled_drift"]
            )
        if result["cancelled_age"]:
            self._refresh_cancelled_age[coin] = (
                self._refresh_cancelled_age.get(coin, 0) + result["cancelled_age"]
            )

        return result

    def get_refresh_stats(self, coin: Optional[str] = None) -> Dict[str, int]:
        """Return cumulative refresh counters for one coin or all coins combined."""
        if coin is not None:
            return {
                "kept": self._refresh_kept.get(coin, 0),
                "cancelled_drift": self._refresh_cancelled_drift.get(coin, 0),
                "cancelled_age": self._refresh_cancelled_age.get(coin, 0),
            }
        return {
            "kept": sum(self._refresh_kept.values()),
            "cancelled_drift": sum(self._refresh_cancelled_drift.values()),
            "cancelled_age": sum(self._refresh_cancelled_age.values()),
        }

    def cancel_all_orders_for_coin(self, coin: str, reason: str = "manual") -> None:
        """Cancel all tracked orders for a coin.

        Called from several distinct control paths — real fill detection,
        drain mode, quiet-hour entry, BBO guard, WS fill feed — and the
        *reason* argument is logged verbatim so observers can tell them
        apart downstream. The legacy log claimed "post-fill cleanup"
        regardless of caller, which was actively misleading on illiquid
        coins where the BBO-guard path dominates.

        Recognised values (free-form, used as a string tag in the log):

        * ``"fill"`` — real fill detection in the strategy main loop
        * ``"ws_fill"`` — WS fill feed instant cancel
        * ``"bbo_guard"`` — BBO guard cancelled both sides on rapid move
        * ``"drain"`` — drain-mode entry (graceful pre-shutdown)
        * ``"quiet_hour"`` — quiet-hour entry (full-stop mode)
        * ``"manual"`` (default) — unspecified caller / tests

        New callers should pick a short snake_case tag and add it here.

        Thread-safe: may be called from the WS fill feed thread.
        """
        with self._lock:
            tracked = list(self._tracked_orders.get(coin, []))
            self._tracked_orders[coin] = []
        if not tracked:
            return

        cancel_requests = [{"coin": coin, "oid": entry[0]} for entry in tracked]
        oid_list = [entry[0] for entry in tracked]
        try:
            cancelled = self.order_manager.bulk_cancel_orders(cancel_requests)
            logger.info(
                f"[mm] Cancelled {cancelled}/{len(cancel_requests)} orders for {coin} "
                f"(reason={reason}): {oid_list}"
            )
        except Exception as e:
            logger.error(
                f"[mm] Error cancelling orders for {coin} (reason={reason}): "
                f"{e}, oids: {oid_list}"
            )

    def cancel_orders_by_side(self, coin: str, side: str) -> None:
        """Cancel tracked orders for a specific side of a coin.

        Only cancels orders matching *side* (``"B"`` for buy, ``"A"`` for sell),
        leaving the opposite side intact.

        Thread-safe: may be called from the WS imbalance guard thread.
        """
        with self._lock:
            tracked = self._tracked_orders.get(coin, [])
            to_cancel = [entry for entry in tracked if entry[1] == side]
            remaining = [entry for entry in tracked if entry[1] != side]
            self._tracked_orders[coin] = remaining

        if not to_cancel:
            return

        cancel_requests = [{"coin": coin, "oid": entry[0]} for entry in to_cancel]
        oid_list = [entry[0] for entry in to_cancel]
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
