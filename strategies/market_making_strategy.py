"""Market-making strategy for Hyperliquid.

Places buy and sell limit orders symmetrically around the current mid
price, optionally closes filled positions immediately, and periodically
refreshes stale orders.

Order tracking and position close management are delegated to
:class:`OrderTracker` and :class:`PositionCloser` respectively.
"""

import logging
import os
from typing import Dict, List, Optional

from strategies.base_strategy import BaseStrategy
from strategies.mm_order_tracker import OrderTracker
from strategies.mm_position_closer import PositionCloser
from order_manager import OrderSide, round_price

logger = logging.getLogger(__name__)


class MarketMakingStrategy(BaseStrategy):

    def __init__(self, market_data_manager, order_manager, config: Dict) -> None:
        super().__init__(market_data_manager, order_manager, config)

        # ---- Configurable parameters ---- #
        self.spread_bps: float = config.get('spread_bps', 5)
        self.order_size_usd: float = config.get('order_size_usd', 50)
        self.max_open_orders: int = config.get('max_open_orders', 4)
        self.close_immediately: bool = config.get('close_immediately', True)
        self.max_positions: int = config.get('max_positions', 3)
        self.maker_only: bool = config.get('maker_only', False)
        self.account_cap_pct: float = config.get('account_cap_pct', 0.05)

        # ---- Delegates ---- #
        self._tracker = OrderTracker(
            order_manager=order_manager,
            refresh_interval_seconds=config.get('refresh_interval_seconds', 30),
            max_open_orders=self.max_open_orders,
        )
        self._closer = PositionCloser(
            order_manager=order_manager,
            market_data=market_data_manager,
            spread_bps=self.spread_bps,
            max_position_age_seconds=config.get('max_position_age_seconds', 120),
            maker_only=self.maker_only,
            taker_fallback_age_seconds=config.get('taker_fallback_age_seconds', None),
        )

    # ------------------------------------------------------------------ #
    #  Main loop override
    # ------------------------------------------------------------------ #

    def run(self, coins: List[str]) -> None:
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
                        self._closer.on_position_closed(coin)
                        continue
                    else:
                        self._closer.manage(coin, self.positions[coin], self.close_position)
                        continue
                else:
                    # Position was closed — clean up tracking
                    self._closer.cleanup_closed(coin)

                # No position — normal MM flow
                close_oid = self._closer.get_close_oid(coin)
                self._tracker.cancel_stale_orders(coin, close_oid=close_oid)

                # Check max positions using active coin count
                active_count = self._tracker.active_coins(
                    self.positions, self._closer.tracked_coins,
                )
                if active_count >= self.max_positions:
                    logger.debug(
                        f"[mm] Max active coins ({active_count}/{self.max_positions}), skipping {coin}"
                    )
                    continue

                if self._tracker.get_order_count(coin) < self.max_open_orders:
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

        return self._apply_account_cap(base_size_usd, market_data.mid_price, cap_pct=self.account_cap_pct)

    # ------------------------------------------------------------------ #
    #  Order placement
    # ------------------------------------------------------------------ #

    def _place_orders(self, coin: str) -> None:
        """Place a buy and a sell limit order symmetrically around mid price."""
        if coin in self._closer.tracked_coins:
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

        current_count = self._tracker.get_order_count(coin)
        orders_to_place = []

        if current_count < self.max_open_orders:
            orders_to_place.append((OrderSide.BUY, buy_price))

        if current_count + len(orders_to_place) < self.max_open_orders:
            orders_to_place.append((OrderSide.SELL, sell_price))

        for side, price in orders_to_place:
            try:
                order = self.order_manager.create_limit_order(
                    coin=coin, side=side, size=size,
                    price=price, reduce_only=False, post_only=True,
                )
                if order and order.id is not None:
                    self._tracker.record_order(coin, order.id, side.value)
                    logger.info(
                        f"[mm] Placed {side.value} limit {coin} "
                        f"size={size} price={price:.6f} (oid={order.id})"
                    )
            except Exception as e:
                logger.error(f"[mm] Failed to place {side.value} order for {coin}: {e}")

    def _get_spread_prices(self, mid_price: float) -> tuple:
        """Return ``(buy_price, sell_price)`` based on ``spread_bps``."""
        offset = mid_price * (self.spread_bps / 10_000)
        buy_price = round_price(mid_price - offset)
        sell_price = round_price(mid_price + offset)
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
