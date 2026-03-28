"""Position close management for market-making strategy.

Handles take-profit placement, max-age force-close, and taker fallback logic.
"""

import logging
import time
from typing import Dict, Optional, Tuple

from order_manager import OrderSide

logger = logging.getLogger(__name__)


class PositionCloser:
    """Manages close orders for filled market-making positions."""

    def __init__(
        self,
        order_manager,
        market_data,
        *,
        spread_bps: float,
        max_position_age_seconds: float,
        maker_only: bool,
        taker_fallback_age_seconds: Optional[float],
    ) -> None:
        self.order_manager = order_manager
        self.market_data = market_data
        self.spread_bps = spread_bps
        self.max_position_age_seconds = max_position_age_seconds
        self.maker_only = maker_only
        self.taker_fallback_age_seconds = taker_fallback_age_seconds

        # coin -> (entry_time, close_oid or None)
        self._open_positions: Dict[str, Tuple[float, Optional[int]]] = {}

    @property
    def tracked_coins(self) -> set:
        return set(self._open_positions.keys())

    def get_close_oid(self, coin: str) -> Optional[int]:
        """Return the close order OID for a coin, or None."""
        entry = self._open_positions.get(coin)
        return entry[1] if entry else None

    def cleanup_closed(self, coin: str) -> None:
        """Clean up tracking when a position has been closed externally."""
        if coin not in self._open_positions:
            return
        close_oid = self._open_positions[coin][1]
        if close_oid is not None:
            try:
                self.order_manager.cancel_order(close_oid, coin)
            except Exception as e:
                logger.debug(f"[mm] Could not cancel leftover close order for {coin}: {e}")
        self._open_positions.pop(coin, None)

    def on_position_closed(self, coin: str) -> None:
        """Remove tracking after an immediate close."""
        self._open_positions.pop(coin, None)

    def manage(self, coin: str, position: Dict, close_position_fn) -> None:
        """Manage take-profit close for an open position.

        Parameters
        ----------
        coin : str
        position : dict with 'size' and 'entry_price' keys
        close_position_fn : callable(coin) that market-closes the position
        """
        size = position['size']
        entry_price = position['entry_price']
        now = time.time()

        # Register position if not tracked
        if coin not in self._open_positions:
            self._open_positions[coin] = (now, None)
            logger.info(f"[mm] Tracking position for {coin}: size={size:.6f} entry={entry_price:.4f}")

        entry_time, close_oid = self._open_positions[coin]
        age = now - entry_time

        # Check if max age exceeded — force close
        if age >= self.max_position_age_seconds:
            self._handle_force_close(coin, size, age, entry_time, close_oid, close_position_fn)
            return

        # Check if close order is still alive
        if close_oid is not None:
            if self._is_order_alive(coin, close_oid):
                return  # Close order still active, wait
            # Close order was filled or cancelled — position should be gone next cycle
            self._open_positions[coin] = (entry_time, None)

        # Place take-profit close order at entry ± spread
        self._place_take_profit(coin, size, entry_price, entry_time)

    def _handle_force_close(
        self, coin: str, size: float, age: float,
        entry_time: float, close_oid: Optional[int],
        close_position_fn,
    ) -> None:
        # Cancel existing close order if any
        if close_oid is not None:
            try:
                self.order_manager.cancel_order(close_oid, coin)
            except Exception as e:
                logger.debug(f"[mm] Could not cancel close order for {coin}: {e}")

        # Check if taker fallback should be used
        use_taker = False
        if not self.maker_only:
            use_taker = True
        elif self.taker_fallback_age_seconds is not None:
            taker_deadline = self.max_position_age_seconds + self.taker_fallback_age_seconds
            if age >= taker_deadline:
                use_taker = True

        if use_taker:
            logger.warning(f"[mm] Position {coin} held {age:.0f}s — force closing with taker order")
            close_position_fn(coin)
            self._open_positions.pop(coin, None)
            return

        # Maker-only close: try limit at mid price (post_only)
        market_data = self.market_data.get_market_data(coin)
        if market_data and market_data.mid_price > 0:
            close_side = OrderSide.SELL if size > 0 else OrderSide.BUY
            close_price = _round_price(market_data.mid_price)
            abs_size = round(abs(size), self.market_data.get_sz_decimals(coin))
            if abs_size > 0:
                try:
                    order = self.order_manager.create_limit_order(
                        coin=coin, side=close_side, size=abs_size,
                        price=close_price, reduce_only=True, post_only=True,
                    )
                    if order and order.id is not None:
                        self._open_positions[coin] = (entry_time, order.id)
                        logger.info(
                            f"[mm] Position {coin} held {age:.0f}s — "
                            f"maker close at {close_price:.6f} (oid={order.id})"
                        )
                        return
                except Exception as e:
                    logger.debug(f"[mm] Maker close failed for {coin}: {e}")

        logger.info(f"[mm] Position {coin} held {age:.0f}s — maker close pending, will retry next cycle")

    def _place_take_profit(self, coin: str, size: float, entry_price: float, entry_time: float) -> None:
        close_side = OrderSide.SELL if size > 0 else OrderSide.BUY
        if size > 0:
            close_price = _round_price(entry_price * (1 + self.spread_bps / 10_000))
        else:
            close_price = _round_price(entry_price * (1 - self.spread_bps / 10_000))

        abs_size = round(abs(size), self.market_data.get_sz_decimals(coin))
        if abs_size <= 0:
            return

        try:
            order = self.order_manager.create_limit_order(
                coin=coin, side=close_side, size=abs_size,
                price=close_price, reduce_only=True, post_only=self.maker_only,
            )
            if order and order.id is not None:
                self._open_positions[coin] = (entry_time, order.id)
                logger.info(
                    f"[mm] Placed take-profit {close_side.value} for {coin} "
                    f"size={abs_size} price={close_price:.6f} (oid={order.id})"
                )
        except Exception as e:
            logger.error(f"[mm] Failed to place close order for {coin}: {e}")

    def _is_order_alive(self, coin: str, oid: int) -> bool:
        try:
            open_orders = self.order_manager.get_open_orders(coin)
            open_oids = {int(o['oid']) for o in open_orders}
            return oid in open_oids
        except Exception as e:
            logger.debug(f"[mm] Could not check close order status for {coin}: {e}")
            return False


def _round_price(px: float) -> float:
    """Round price to 5 significant figures and 6 decimal places (Hyperliquid perp format)."""
    return round(float(f"{px:.5g}"), 6)
