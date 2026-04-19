"""Market-making strategy for Hyperliquid.

Places buy and sell limit orders symmetrically around the current mid
price, optionally closes filled positions immediately, and periodically
refreshes stale orders.

Order tracking and position close management are delegated to
:class:`OrderTracker` and :class:`PositionCloser` respectively.
"""

import logging
import os
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional

from strategies.base_strategy import BaseStrategy
from strategies.mm_order_tracker import OrderTracker
from strategies.mm_position_closer import PositionCloser
from order_manager import BBO_OFFSET, OrderSide, round_price
from rate_limiter import API_ERRORS

logger = logging.getLogger(__name__)

# Minimum offset (in fraction) from BBO to avoid post-only rejections


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
        self.bbo_mode: bool = config.get('bbo_mode', False)
        raw_offset = config.get('bbo_offset_bps', None)
        if raw_offset is not None:
            self.bbo_offset_bps = max(0.0, float(raw_offset))
        elif self.bbo_mode and self.maker_only:
            # Default to small offset to reduce Alo rejection risk in fast markets
            self.bbo_offset_bps = 0.1
        else:
            self.bbo_offset_bps = 0.0
        self.inventory_skew_bps: float = config.get('inventory_skew_bps', 0)
        self.inventory_skew_cap: float = config.get('inventory_skew_cap', 3.0)

        # ---- L2 book imbalance guard ---- #
        self.imbalance_threshold: float = config.get('imbalance_threshold', 0.0)
        if not (0 <= self.imbalance_threshold <= 1):
            raise ValueError(f"imbalance_threshold must be in [0, 1], got {self.imbalance_threshold}")

        # ---- Per-coin loss streak cooldown ---- #
        self.loss_streak_limit: int = config.get('loss_streak_limit', 0)
        self.loss_streak_cooldown: float = config.get('loss_streak_cooldown', 300)
        if self.loss_streak_limit < 0:
            raise ValueError(f"loss_streak_limit must be >= 0, got {self.loss_streak_limit}")
        if self.loss_streak_limit > 0 and self.loss_streak_cooldown <= 0:
            raise ValueError(f"loss_streak_cooldown must be > 0 when limit is set, got {self.loss_streak_cooldown}")
        self._loss_streaks: Dict[str, int] = defaultdict(int)  # coin -> consecutive losses
        self._coin_cooldown_until: Dict[str, float] = {}  # coin -> monotonic deadline

        # ---- Volatility-adjusted BBO offset ---- #
        self.vol_adjust_enabled: bool = config.get('vol_adjust_enabled', False)
        self.vol_adjust_multiplier: float = config.get('vol_adjust_multiplier', 2.0)
        self.vol_lookback: int = config.get('vol_lookback', 30)
        self.vol_adjust_max_offset: float = config.get('vol_adjust_max_offset', 50.0)
        self._recent_mids: Dict[str, deque] = {}  # coin -> deque of recent mid prices

        # ---- Fill rate tracking ---- #
        self._orders_placed: int = 0
        self._fills_detected: int = 0
        self._orders_placed_per_coin: Dict[str, int] = defaultdict(int)
        self._fills_per_coin: Dict[str, int] = defaultdict(int)
        self._fill_rate_log_interval: float = config.get('fill_rate_log_interval', 300)
        self._last_fill_rate_log: float = 0.0
        self._prev_position_coins: set = set()  # coins that had positions last cycle
        self._prev_positions: Dict[str, Dict] = {}  # snapshot for loss streak detection

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
            aggressive_loss_bps=config.get('aggressive_loss_bps', 1.0),
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

        # Detect new fills: a position appearing for a coin that had no
        # position last cycle means at least one maker order was filled.
        # NOTE: This is a coarse approximation. It undercounts when multiple
        # fills occur within a single cycle or when close_immediately=True
        # (positions are closed before the next detection pass). Sufficient
        # for spread-tuning observability; not intended as exact accounting.
        current_position_coins = set()
        for coin in coins:
            if coin in self.positions and abs(self.positions[coin].get('size', 0)) > 0:
                current_position_coins.add(coin)
        new_fills = current_position_coins - self._prev_position_coins
        for coin in new_fills:
            self._fills_detected += 1
            self._fills_per_coin[coin] += 1
            # Cancel opposite-side orders for newly filled coins to prevent
            # double-filling which doubles adverse selection cost.
            # NOTE: Does not fire when close_immediately=True because
            # positions are closed before the next detection pass.
            self._tracker.cancel_all_orders_for_coin(coin)
            logger.info(f"[mm] Cancelled orders for {coin} after fill (prevent double-fill)")

        # Track loss streaks: detect coins that just closed (had position last cycle, not now)
        if self.loss_streak_limit > 0:
            just_closed = self._prev_position_coins - current_position_coins
            for coin in just_closed:
                last_pos = self._prev_positions.get(coin, {})
                entry_px = float(last_pos.get('entryPx', 0))
                size = float(last_pos.get('size', 0))
                # Estimate close PnL from entry price and current mid price.
                # This is an approximation — actual close price may differ
                # slightly, but the sign (win/loss) is reliable enough for
                # streak detection since closes happen near current price.
                md = self.market_data.get_market_data(coin)
                if md and entry_px > 0 and size != 0:
                    estimated_pnl = (md.mid_price - entry_px) * size
                else:
                    estimated_pnl = float(last_pos.get('unrealizedPnl', 0))

                if estimated_pnl < 0:
                    self._loss_streaks[coin] += 1
                    streak = self._loss_streaks[coin]
                    if streak >= self.loss_streak_limit:
                        deadline = time.monotonic() + self.loss_streak_cooldown
                        self._coin_cooldown_until[coin] = deadline
                        logger.info(
                            f"[mm] {coin} hit {streak} consecutive losses, "
                            f"cooldown {self.loss_streak_cooldown}s"
                        )
                else:
                    self._loss_streaks[coin] = 0

        self._prev_positions = {c: dict(self.positions[c]) for c in self.positions if self.positions.get(c)}
        self._prev_position_coins = current_position_coins

        self._log_fill_rate()

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

                # Per-coin loss streak cooldown
                if self.loss_streak_limit > 0:
                    cooldown_deadline = self._coin_cooldown_until.get(coin)
                    now = time.monotonic()
                    if cooldown_deadline and now < cooldown_deadline:
                        remaining = cooldown_deadline - now
                        logger.debug(f"[mm] {coin} in cooldown ({remaining:.0f}s left)")
                        continue
                    elif cooldown_deadline:
                        # Cooldown expired — reset
                        del self._coin_cooldown_until[coin]
                        self._loss_streaks[coin] = 0
                        logger.info(f"[mm] {coin} cooldown expired, resuming")

                if self._tracker.get_order_count(coin) < self.max_open_orders:
                    self._place_orders(coin)

            except API_ERRORS as e:
                logger.error(f"[mm] Error processing {coin}: {e}")

        # Per-cycle log with inventory skew info.
        # Format differs from base strategy's [cycle] (no signals_generated)
        # because MM uses a position-based flow, not signal-based.
        coin_statuses = []
        active_positions = 0
        for coin in coins:
            pos = self.positions.get(coin)
            if pos and pos['size'] != 0:
                active_positions += 1
                md = self.market_data.get_market_data(coin)
                mid = md.mid_price if md else 0
                skew = self._calculate_inventory_skew(coin, mid)
                if abs(skew) > 0:
                    coin_statuses.append(f"{coin}:skew{skew:+.1f}bp")
                else:
                    coin_statuses.append(f"{coin}:pos")
            else:
                # Show vol-adjusted offset when it differs from base (read-only)
                if self.vol_adjust_enabled and self.bbo_mode:
                    adj = self._get_volatility_adjusted_offset(coin)
                    if abs(adj - self.bbo_offset_bps) > 0.01:
                        coin_statuses.append(f"{coin}:idle(off={adj:.1f}bp)")
                        continue
                coin_statuses.append(f"{coin}:idle")

        max_display = getattr(self, '_max_coin_status_display', 10)
        if len(coin_statuses) <= max_display:
            status_str = " | ".join(coin_statuses)
        else:
            shown = coin_statuses[:max_display]
            status_str = " | ".join(shown) + f" ... +{len(coin_statuses) - max_display} more"
        logger.info(
            f"[cycle] {len(coins)} coins, {active_positions} pos | {status_str}"
        )

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

    def _record_mid_price(self, coin: str, mid_price: float) -> None:
        """Record a mid price for volatility tracking. Call once per cycle."""
        if not self.vol_adjust_enabled:
            return
        if coin not in self._recent_mids:
            self._recent_mids[coin] = deque(maxlen=self.vol_lookback)
        self._recent_mids[coin].append(mid_price)

    def _get_volatility_adjusted_offset(self, coin: str) -> float:
        """Return BBO offset adjusted for recent volatility.

        Read-only — does not mutate state. Call _record_mid_price()
        separately to update the price history.
        """
        if not self.vol_adjust_enabled or not self.bbo_mode:
            return self.bbo_offset_bps

        mids = self._recent_mids.get(coin)
        if not mids or len(mids) < 5:
            return self.bbo_offset_bps

        # Calculate realized volatility (average absolute return in bps)
        returns_bps = []
        for i in range(1, len(mids)):
            ret = abs(mids[i] - mids[i - 1]) / mids[i - 1] * 10_000
            returns_bps.append(ret)

        avg_move_bps = sum(returns_bps) / len(returns_bps)

        # Scale offset with cap to prevent extreme values during flash crashes
        adjusted = self.bbo_offset_bps + self.vol_adjust_multiplier * avg_move_bps
        return min(adjusted, self.vol_adjust_max_offset)

    def _place_orders(self, coin: str) -> None:
        """Place a buy and a sell limit order.

        In BBO mode, orders are placed at or near the best bid/ask.
        Otherwise, orders are placed symmetrically around mid price at
        ``spread_bps``.  Uses ``bulk_place_orders`` for a single API call.
        """
        from order_manager import Order

        if coin in self._closer.tracked_coins:
            return

        market_data = self.market_data.get_market_data(coin)
        if not market_data:
            logger.warning(f"[mm] No market data for {coin}, skipping")
            return

        mid_price = market_data.mid_price
        if mid_price <= 0:
            return

        rp = self.market_data.price_rounding_params(coin)

        if self.bbo_mode and market_data.bid > 0 and market_data.ask > 0:
            # BBO-following mode: place at/near best bid and ask
            self._record_mid_price(coin, mid_price)
            effective_offset_bps = self._get_volatility_adjusted_offset(coin)
            offset = effective_offset_bps / 10_000
            buy_price = round_price(market_data.bid * (1 - offset), *rp)
            sell_price = round_price(market_data.ask * (1 + offset), *rp)
        else:
            # Fallback: mid ± spread. Also used when BBO is unavailable
            # (bid/ask=0) even in bbo_mode. Maker-only clamping below
            # ensures Alo orders don't cross the spread.
            raw_buy, raw_sell = self._get_spread_prices(mid_price)
            buy_price = round_price(raw_buy, *rp)
            sell_price = round_price(raw_sell, *rp)
            # Clamp prices to stay outside BBO for maker-only (Alo) orders
            if self.maker_only and market_data.bid > 0 and market_data.ask > 0:
                if buy_price >= market_data.bid:
                    buy_price = round_price(market_data.bid * (1 - BBO_OFFSET), *rp)
                if sell_price <= market_data.ask:
                    sell_price = round_price(market_data.ask * (1 + BBO_OFFSET), *rp)

        # Inventory skew: shift both prices to encourage position reduction.
        # Applied after BBO/spread pricing intentionally — skew may push
        # prices beyond BBO bounds (e.g. sell below ask) which is desired
        # to accelerate inventory reduction via more aggressive fills.
        skew = self._calculate_inventory_skew(coin, mid_price)
        if skew != 0.0:
            skew_mult = skew / 10_000
            buy_price = round_price(buy_price * (1 - skew_mult), *rp)
            sell_price = round_price(sell_price * (1 - skew_mult), *rp)
            logger.debug(f"[mm] Inventory skew {coin}: {skew:.1f}bps")

        size = self.calculate_position_size(coin, {})
        if size <= 0:
            logger.debug(f"[mm] Position size is 0 for {coin}, skipping")
            return

        size = self.market_data.round_size(coin, size)
        if size <= 0:
            return

        current_count = self._tracker.get_order_count(coin)
        sides_and_prices = []

        # L2 book imbalance guard: skip the side that is likely to get adversely selected.
        # book_imbalance > 0 = bid-heavy (buy pressure) → selling is risky (price likely going up)
        # book_imbalance < 0 = ask-heavy (sell pressure) → buying is risky (price likely going down)
        skip_buy = False
        skip_sell = False
        if self.imbalance_threshold > 0:
            imb = market_data.book_imbalance
            if imb < -self.imbalance_threshold:
                skip_buy = True
                logger.debug(f"[mm] {coin} skipping BUY (book imbalance {imb:.2f})")
            elif imb > self.imbalance_threshold:
                skip_sell = True
                logger.debug(f"[mm] {coin} skipping SELL (book imbalance {imb:.2f})")

        if current_count < self.max_open_orders and not skip_buy:
            sides_and_prices.append((OrderSide.BUY, buy_price))

        if current_count + len(sides_and_prices) < self.max_open_orders and not skip_sell:
            sides_and_prices.append((OrderSide.SELL, sell_price))

        if not sides_and_prices:
            return

        order_objects = [
            Order(
                id=None, coin=coin, side=side, size=size,
                price=price,
                order_type={"limit": {"tif": "Alo"}},
                reduce_only=False,
            )
            for side, price in sides_and_prices
        ]

        results = self.order_manager.bulk_place_orders(order_objects)

        for (side, price), order in zip(sides_and_prices, results):
            if order and order.id is not None:
                self._tracker.record_order(coin, order.id, side.value)
                self._orders_placed += 1
                self._orders_placed_per_coin[coin] += 1
                logger.info(
                    f"[mm] Placed {side.value} limit {coin} "
                    f"size={size} price={price:.6f} (oid={order.id})"
                )

    def _get_spread_prices(self, mid_price: float) -> tuple:
        """Return raw ``(buy_price, sell_price)`` based on ``spread_bps``.

        Prices are not rounded — callers apply :func:`round_price` with
        the appropriate asset parameters.
        """
        offset = mid_price * (self.spread_bps / 10_000)
        return mid_price - offset, mid_price + offset

    def _calculate_inventory_skew(self, coin: str, mid_price: float) -> float:
        """Calculate price skew in bps based on current inventory.

        Positive skew shifts both prices down (encourages selling when long).
        Negative skew shifts both prices up (encourages buying when short).
        """
        if not self.inventory_skew_bps:
            return 0.0

        position = self.positions.get(coin)
        if not position or position['size'] == 0:
            return 0.0

        size = position['size']
        if mid_price <= 0:
            return 0.0

        # Normalize position value relative to order_size_usd
        position_value = abs(size) * mid_price
        normalized = position_value / self.order_size_usd
        normalized = min(normalized, self.inventory_skew_cap)

        # Long = positive skew (shift down), Short = negative skew (shift up)
        direction = 1.0 if size > 0 else -1.0
        return direction * normalized * self.inventory_skew_bps

    # ------------------------------------------------------------------ #
    #  Fill rate observability
    # ------------------------------------------------------------------ #

    def _log_fill_rate(self) -> None:
        """Log fill rate statistics periodically."""
        now = time.monotonic()
        if now - self._last_fill_rate_log < self._fill_rate_log_interval:
            return
        self._last_fill_rate_log = now

        if self._orders_placed == 0:
            return

        fill_rate = (self._fills_detected / self._orders_placed) * 100
        logger.info(
            "[mm] Fill rate: %d/%d (%.1f%%) | per-coin fills: %s",
            self._fills_detected,
            self._orders_placed,
            fill_rate,
            dict(self._fills_per_coin) if self._fills_per_coin else "none",
        )

        # Reset counters so next log line reflects the latest window only
        self._orders_placed = 0
        self._fills_detected = 0
        self._orders_placed_per_coin.clear()
        self._fills_per_coin.clear()

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
