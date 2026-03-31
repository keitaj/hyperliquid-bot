from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import logging
import pandas as pd
from market_data import MarketDataManager, MarketData
from order_manager import OrderManager, OrderSide, round_price
from account_utils import get_account_snapshot
from rate_limiter import API_ERRORS

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    def __init__(
        self,
        market_data_manager: MarketDataManager,
        order_manager: OrderManager,
        config: Dict
    ) -> None:
        self.market_data = market_data_manager
        self.order_manager = order_manager
        self.config = config
        self.positions: Dict[str, Dict] = {}

    @abstractmethod
    def generate_signals(self, coin: str) -> Optional[Dict]:
        pass

    @abstractmethod
    def calculate_position_size(self, coin: str, signal: Dict) -> float:
        pass

    # ------------------------------------------------------------------ #
    # Shared signal helpers
    # ------------------------------------------------------------------ #

    def _has_position(self, coin: str) -> bool:
        """Return True if *coin* has a non-zero open position."""
        return coin in self.positions and self.positions[coin]['size'] != 0

    def _get_candles_or_none(self, coin: str, min_periods: int,
                             interval: Optional[str] = None,
                             lookback: Optional[int] = None) -> Optional[pd.DataFrame]:
        """Fetch candles and return None if fewer than *min_periods* rows."""
        candles = self.market_data.get_candles(
            coin=coin,
            interval=interval or getattr(self, 'candle_interval', '15m'),
            lookback=lookback or getattr(self, 'lookback', min_periods + 10),
        )
        if len(candles) < min_periods:
            return None
        return candles

    # ------------------------------------------------------------------ #
    # Shared position-sizing helpers
    # ------------------------------------------------------------------ #

    def _check_max_positions(self, coin: str) -> bool:
        """Returns True (and logs) if already at max open positions for a new coin."""
        max_pos = getattr(self, 'max_positions', None)
        if max_pos is not None and len(self.positions) >= max_pos and coin not in self.positions:
            logger.info(f"Max positions reached, skipping {coin}")
            return True
        return False

    def _apply_account_cap(self, base_size_usd: float, mid_price: float, cap_pct: float = 0.1) -> float:
        """
        Convert a USD size to coin units, capping at cap_pct of account value.
        With Portfolio Margin, spot stablecoin balances count as collateral.
        """
        try:
            snapshot = get_account_snapshot(
                self.order_manager.info,
                self.order_manager.account_address,
            )
            if snapshot.account_value > 0:
                max_size_usd = snapshot.account_value * cap_pct
                if base_size_usd > max_size_usd:
                    return max_size_usd / mid_price
        except API_ERRORS as e:
            logger.warning(f"Could not apply account cap: {e}")
        return base_size_usd / mid_price

    def execute_signal(self, coin: str, signal: Dict) -> None:
        if not signal:
            return

        try:
            side = signal.get('side')
            if not side:
                return

            position_size = self.calculate_position_size(coin, signal)
            if position_size <= 0:
                return

            # Round position size to correct decimals
            sz_decimals = self.market_data.get_sz_decimals(coin)
            position_size = round(position_size, sz_decimals)

            market_data = self.market_data.get_market_data(coin)
            if not market_data:
                logger.warning(f"No market data available for {coin}")
                return

            if signal.get('order_type') == 'market':
                order = self.order_manager.create_market_order(
                    coin=coin,
                    side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                    size=position_size,
                    reduce_only=signal.get('reduce_only', False)
                )
            else:
                price = self._calculate_limit_price(market_data, side)
                order = self.order_manager.create_limit_order(
                    coin=coin,
                    side=OrderSide.BUY if side == 'buy' else OrderSide.SELL,
                    size=position_size,
                    price=price,
                    reduce_only=signal.get('reduce_only', False),
                    post_only=signal.get('post_only', True)
                )

            if order:
                logger.info(
                    f"Executed {side} order for {coin}: size={position_size} (rounded to {sz_decimals} decimals)")

        except API_ERRORS as e:
            logger.error(f"Error executing signal for {coin}: {e}")

    def _calculate_limit_price(self, market_data: MarketData, side: str) -> float:
        if side == 'buy':
            return round_price(market_data.bid)
        else:
            return round_price(market_data.ask)

    def update_positions(self) -> None:
        self.positions = {}
        all_positions = self.order_manager.get_all_positions()

        for position in all_positions:
            coin = position['coin']
            self.positions[coin] = {
                'size': float(position['szi']),
                'entry_price': float(position['entryPx']),
                'unrealized_pnl': float(position['unrealizedPnl']),
                'margin_used': float(position['marginUsed'])
            }

    def should_close_position(self, coin: str) -> bool:
        if coin not in self.positions:
            return False

        position = self.positions[coin]
        market_data = self.market_data.get_market_data(coin)

        if not market_data:
            return False

        pnl_percent = (position['unrealized_pnl'] / position['margin_used']) * 100

        if pnl_percent >= self.config.get('take_profit_percent', 10):
            logger.info(f"Take profit triggered for {coin}: {pnl_percent:.2f}%")
            return True

        if pnl_percent <= -self.config.get('stop_loss_percent', 5):
            logger.info(f"Stop loss triggered for {coin}: {pnl_percent:.2f}%")
            return True

        return False

    def close_position(self, coin: str) -> None:
        position = self.positions.get(coin)
        if not position:
            return

        size = abs(position['size'])
        side = OrderSide.SELL if position['size'] > 0 else OrderSide.BUY

        # Round position size to correct decimals
        sz_decimals = self.market_data.get_sz_decimals(coin)
        size = round(size, sz_decimals)

        order = self.order_manager.create_market_order(
            coin=coin,
            side=side,
            size=size,
            reduce_only=True
        )

        if order:
            logger.info(f"Closed position for {coin}: size={size} (rounded to {sz_decimals} decimals)")

    def run(self, coins: List[str]) -> None:
        self.update_positions()

        for coin in coins:
            if self.should_close_position(coin):
                self.close_position(coin)
            else:
                signal = self.generate_signals(coin)
                if signal:
                    self.execute_signal(coin, signal)
