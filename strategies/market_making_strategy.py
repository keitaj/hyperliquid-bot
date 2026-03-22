import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from strategies.base_strategy import BaseStrategy
from order_manager import OrderSide

logger = logging.getLogger(__name__)


class MarketMakingStrategy(BaseStrategy):
    """Simple market-making strategy.

    Places buy and sell limit orders symmetrically around the current mid
    price, optionally closes filled positions immediately, and periodically
    refreshes stale orders.  Works in any market condition.

    When ``close_immediately`` is False, filled positions are closed via a
    take-profit limit order at entry ± spread.  If the take-profit is not
    filled within ``max_position_age_seconds``, the position is closed at
    the current mid price to prevent indefinite holding.

    All parameters are configurable via the strategy config dict (populated
    from CLI flags or environment variables).
    """

    def __init__(self, market_data_manager, order_manager, config: Dict):
        super().__init__(market_data_manager, order_manager, config)

        # ---- Configurable parameters ---- #
        self.spread_bps: float = config.get('spread_bps', 5)
        self.order_size_usd: float = config.get('order_size_usd', 50)
        self.max_open_orders: int = config.get('max_open_orders', 4)
        self.refresh_interval_seconds: float = config.get('refresh_interval_seconds', 30)
        self.close_immediately: bool = config.get('close_immediately', True)
        self.max_positions: int = config.get('max_positions', 3)
        self.max_position_age_seconds: float = config.get('max_position_age_seconds', 120)
        self.maker_only: bool = config.get('maker_only', False)
        # Seconds after max_position_age to fall back to taker for force-close.
        # 0 = taker fallback at max_position_age (immediate), None = never use taker.
        self.taker_fallback_age_seconds: float = config.get('taker_fallback_age_seconds', None)
        self.account_cap_pct: float = config.get('account_cap_pct', 0.05)

        # ---- Internal state ---- #
        self._last_order_time: Dict[str, float] = {}
        # coin -> list of (oid, side, place_time)
        self._tracked_orders: Dict[str, list] = {}
        # coin -> (entry_time, close_oid or None)
        self._open_positions: Dict[str, Tuple[float, Optional[int]]] = {}

    # ------------------------------------------------------------------ #
    #  Main loop override
    # ------------------------------------------------------------------ #

    def run(self, coins: List[str]):
        """Override the default signal-based loop with a market-making loop.

        Flow per coin:
        1. If position exists and close_immediately: market-close it.
        2. If position exists and not close_immediately: manage take-profit.
        3. Cancel stale orders.
        4. Place new buy + sell limit orders if no position and capacity.
        """
        self.update_positions()

        for coin in coins:
            try:
                has_position = coin in self.positions and abs(self.positions[coin]['size']) > 0

                if has_position:
                    if self.close_immediately:
                        logger.info(
                            f"[mm] Closing position for {coin}: "
                            f"size={self.positions[coin]['size']:.6f}"
                        )
                        self.close_position(coin)
                        self._open_positions.pop(coin, None)
                        continue
                    else:
                        # Manage take-profit close for existing position
                        self._manage_position_close(coin)
                        continue
                else:
                    # Position was closed — clean up tracking
                    if coin in self._open_positions:
                        close_oid = self._open_positions[coin][1]
                        if close_oid is not None:
                            # Cancel leftover close order if position is gone
                            try:
                                self.order_manager.cancel_order(close_oid, coin)
                            except Exception as e:
                                logger.debug(f"[mm] Could not cancel leftover close order for {coin}: {e}")
                        self._open_positions.pop(coin, None)

                # No position — normal MM flow
                self._cancel_stale_orders(coin)

                current_orders = self._tracked_orders.get(coin, [])
                if len(current_orders) < self.max_open_orders:
                    self._place_orders(coin)

            except Exception as e:
                logger.error(f"[mm] Error processing {coin}: {e}")

    # ------------------------------------------------------------------ #
    #  Position close management (no-close-immediately mode)
    # ------------------------------------------------------------------ #

    def _manage_position_close(self, coin: str):
        """Ensure a take-profit close order exists for an open position.

        If the position has been held longer than ``max_position_age_seconds``,
        close it at mid price to prevent indefinite holding.
        """
        pos = self.positions[coin]
        size = pos['size']
        entry_price = pos['entry_price']
        now = time.time()

        # Register position if not tracked
        if coin not in self._open_positions:
            self._open_positions[coin] = (now, None)
            logger.info(f"[mm] Tracking position for {coin}: size={size:.6f} entry={entry_price:.4f}")

        entry_time, close_oid = self._open_positions[coin]
        age = now - entry_time

        # Check if max age exceeded — force close
        if age >= self.max_position_age_seconds:
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
                logger.warning(
                    f"[mm] Position {coin} held {age:.0f}s — "
                    f"force closing with taker order"
                )
                self.close_position(coin)
                self._open_positions.pop(coin, None)
                return

            # Maker-only close: try limit at mid price (post_only)
            market_data = self.market_data.get_market_data(coin)
            if market_data and market_data.mid_price > 0:
                close_side = OrderSide.SELL if size > 0 else OrderSide.BUY
                close_price = self._round_price(market_data.mid_price)
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

            logger.info(
                f"[mm] Position {coin} held {age:.0f}s — "
                f"maker close pending, will retry next cycle"
            )
            return

        # Check if close order is still alive
        if close_oid is not None:
            try:
                open_orders = self.order_manager.get_open_orders(coin)
                open_oids = {int(o['oid']) for o in open_orders}
                if close_oid in open_oids:
                    return  # Close order still active, wait
            except Exception as e:
                logger.debug(f"[mm] Could not check close order status for {coin}: {e}")
            # Close order was filled or cancelled — position should be gone next cycle
            self._open_positions[coin] = (entry_time, None)

        # Place take-profit close order at entry ± spread
        close_side = OrderSide.SELL if size > 0 else OrderSide.BUY
        if size > 0:
            close_price = self._round_price(entry_price * (1 + self.spread_bps / 10_000))
        else:
            close_price = self._round_price(entry_price * (1 - self.spread_bps / 10_000))

        abs_size = abs(size)
        sz_decimals = self.market_data.get_sz_decimals(coin)
        abs_size = round(abs_size, sz_decimals)

        if abs_size <= 0:
            return

        try:
            order = self.order_manager.create_limit_order(
                coin=coin,
                side=close_side,
                size=abs_size,
                price=close_price,
                reduce_only=True,
                post_only=self.maker_only,
            )
            if order and order.id is not None:
                self._open_positions[coin] = (entry_time, order.id)
                logger.info(
                    f"[mm] Placed take-profit {close_side.value} for {coin} "
                    f"size={abs_size} price={close_price:.6f} (oid={order.id})"
                )
        except Exception as e:
            logger.error(f"[mm] Failed to place close order for {coin}: {e}")

    # ------------------------------------------------------------------ #
    #  Stub implementations for abstract methods (not used in run())
    # ------------------------------------------------------------------ #

    def generate_signals(self, coin: str) -> Optional[Dict]:
        """Not used -- the market-making loop is driven by ``run()``."""
        return None

    def calculate_position_size(self, coin: str, signal: Dict) -> float:
        """Calculate order size in coin units, respecting the risk-level multiplier."""
        market_data = self.market_data.get_market_data(coin)
        if not market_data or market_data.mid_price <= 0:
            return 0.0

        base_size_usd = self.order_size_usd

        # Apply risk-level multiplier (green=100%, yellow=50%, red/black=0%)
        multiplier = self._get_risk_multiplier()
        base_size_usd *= multiplier
        if base_size_usd <= 0:
            return 0.0

        return self._apply_account_cap(base_size_usd, market_data.mid_price, cap_pct=self.account_cap_pct)

    # ------------------------------------------------------------------ #
    #  Order placement and management
    # ------------------------------------------------------------------ #

    def _place_orders(self, coin: str):
        """Place a buy and a sell limit order symmetrically around mid price.

        Skip placing new orders if we already have a position in this coin
        (position is managed by _manage_position_close instead).
        """
        if coin in self._open_positions:
            return

        if self._check_max_positions(coin):
            return

        market_data = self.market_data.get_market_data(coin)
        if not market_data:
            logger.warning(f"[mm] No market data for {coin}, skipping")
            return

        mid_price = market_data.mid_price
        if mid_price <= 0:
            return

        buy_price, sell_price = self._get_spread_prices(mid_price)

        size = self.calculate_position_size(coin, {})
        if size <= 0:
            logger.debug(f"[mm] Position size is 0 for {coin}, skipping")
            return

        sz_decimals = self.market_data.get_sz_decimals(coin)
        size = round(size, sz_decimals)
        if size <= 0:
            return

        now = time.time()
        if coin not in self._tracked_orders:
            self._tracked_orders[coin] = []

        current_count = len(self._tracked_orders[coin])
        orders_to_place = []

        if current_count < self.max_open_orders:
            orders_to_place.append((OrderSide.BUY, buy_price))

        if current_count + len(orders_to_place) < self.max_open_orders:
            orders_to_place.append((OrderSide.SELL, sell_price))

        for side, price in orders_to_place:
            try:
                order = self.order_manager.create_limit_order(
                    coin=coin,
                    side=side,
                    size=size,
                    price=price,
                    reduce_only=False,
                    post_only=True,
                )
                if order and order.id is not None:
                    self._tracked_orders[coin].append(
                        (order.id, side.value, now)
                    )
                    logger.info(
                        f"[mm] Placed {side.value} limit {coin} "
                        f"size={size} price={price:.6f} (oid={order.id})"
                    )
            except Exception as e:
                logger.error(
                    f"[mm] Failed to place {side.value} order "
                    f"for {coin}: {e}"
                )

        self._last_order_time[coin] = now

    def _cancel_stale_orders(self, coin: str):
        """Cancel orders older than ``refresh_interval_seconds`` and
        remove filled/cancelled orders from tracking.

        Does NOT cancel close orders managed by _manage_position_close.
        """
        tracked = self._tracked_orders.get(coin, [])
        if not tracked:
            return

        now = time.time()
        still_active = []

        # Protect close order OIDs from cancellation
        close_oid = None
        if coin in self._open_positions:
            close_oid = self._open_positions[coin][1]

        try:
            open_orders = self.order_manager.get_open_orders(coin)
            open_oids = {int(o['oid']) for o in open_orders}
        except Exception as e:
            logger.error(f"[mm] Error fetching open orders for {coin}: {e}")
            return

        for oid, side, place_time in tracked:
            if oid not in open_oids:
                logger.debug(
                    f"[mm] Order {oid} ({side} {coin}) no longer open"
                )
                continue

            # Never cancel a close order from here
            if close_oid is not None and oid == close_oid:
                still_active.append((oid, side, place_time))
                continue

            age = now - place_time
            if age >= self.refresh_interval_seconds:
                try:
                    self.order_manager.cancel_order(oid, coin)
                    logger.info(
                        f"[mm] Cancelled stale {side} order {oid} "
                        f"for {coin} (age={age:.0f}s)"
                    )
                except Exception as e:
                    logger.error(
                        f"[mm] Failed to cancel order {oid} "
                        f"for {coin}: {e}"
                    )
            else:
                still_active.append((oid, side, place_time))

        self._tracked_orders[coin] = still_active

    def _get_spread_prices(self, mid_price: float) -> tuple:
        """Return ``(buy_price, sell_price)`` based on ``spread_bps``."""
        offset = mid_price * (self.spread_bps / 10_000)
        buy_price = self._round_price(mid_price - offset)
        sell_price = self._round_price(mid_price + offset)
        return buy_price, sell_price

    @staticmethod
    def _round_price(px: float) -> float:
        """Round price to 5 significant figures and 6 decimal places (Hyperliquid perp format)."""
        return round(float(f"{px:.5g}"), 6)

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_risk_multiplier() -> float:
        """Read the runtime RISK_LEVEL env var and return a sizing multiplier."""
        level = os.getenv("RISK_LEVEL", "green").lower().strip()
        return {"green": 1.0, "yellow": 0.5, "red": 0.0, "black": 0.0}.get(
            level, 1.0
        )
