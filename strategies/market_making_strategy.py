import logging
import os
import time
from typing import Dict, List, Optional

from strategies.base_strategy import BaseStrategy
from order_manager import OrderSide
from rate_limiter import api_wrapper

logger = logging.getLogger(__name__)


class MarketMakingStrategy(BaseStrategy):
    """Simple market-making strategy.

    Places buy and sell limit orders symmetrically around the current mid
    price, optionally closes filled positions immediately, and periodically
    refreshes stale orders.  Works in any market condition.

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

        # ---- Internal state ---- #
        self._last_order_time: Dict[str, float] = {}
        self._tracked_orders: Dict[str, list] = {}

    # ------------------------------------------------------------------ #
    #  Main loop override
    # ------------------------------------------------------------------ #

    def run(self, coins: List[str]):
        """Override the default signal-based loop with a market-making loop.

        Flow per coin:
        1. Close any existing position immediately (if configured).
        2. Cancel stale orders if the refresh interval has elapsed.
        3. Place buy + sell limit orders around mid price.
        """
        self.update_positions()

        for coin in coins:
            try:
                # Step 1: close existing positions if configured
                if coin in self.positions and self.close_immediately:
                    pos = self.positions[coin]
                    if abs(pos['size']) > 0:
                        logger.info(
                            f"[mm] Closing position for {coin}: "
                            f"size={pos['size']:.6f}"
                        )
                        self.close_position(coin)
                        continue

                # Step 2: cancel stale orders
                self._cancel_stale_orders(coin)

                # Step 3: place new orders if we have capacity
                current_orders = self._tracked_orders.get(coin, [])
                if len(current_orders) < self.max_open_orders:
                    self._place_orders(coin)

            except Exception as e:
                logger.error(f"[mm] Error processing {coin}: {e}")

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

        return self._apply_account_cap(base_size_usd, market_data.mid_price, cap_pct=0.05)

    # ------------------------------------------------------------------ #
    #  Order placement and management
    # ------------------------------------------------------------------ #

    def _place_orders(self, coin: str):
        """Place a buy and a sell limit order symmetrically around mid price."""

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
        remove filled/cancelled orders from tracking."""

        tracked = self._tracked_orders.get(coin, [])
        if not tracked:
            return

        now = time.time()
        still_active = []

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
        """Return ``(buy_price, sell_price)`` based on ``spread_bps``.

        ``spread_bps`` is the one-sided spread in basis points, so the
        full bid/ask spread is ``2 * spread_bps`` bps.
        """
        offset = mid_price * (self.spread_bps / 10_000)
        buy_price = round(mid_price - offset, 8)
        sell_price = round(mid_price + offset, 8)
        return buy_price, sell_price

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
