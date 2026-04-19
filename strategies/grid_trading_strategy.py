import logging
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy
from rate_limiter import API_ERRORS
from order_manager import round_price

logger = logging.getLogger(__name__)


class GridTradingStrategy(BaseStrategy):
    """Grid trading strategy for ranging markets.

    Places buy and sell limit orders at regular grid intervals around the
    current price.  Profits accumulate as price oscillates within the range.
    Automatically recalculates grid levels when the market moves.
    """

    def __init__(self, market_data_manager, order_manager, config):
        super().__init__(market_data_manager, order_manager, config)
        self.grid_levels = config.get('grid_levels', 10)
        self.grid_spacing_pct = config.get('grid_spacing_pct', 0.5)
        self.position_size_per_grid = config.get('position_size_per_grid', 50)
        self.max_positions = config.get('max_positions', 5)
        self.range_period = config.get('range_period', 100)
        self.candle_interval = config.get('candle_interval', '15m')
        self.range_pct_threshold = config.get('range_pct_threshold', 10)
        self.volatility_threshold = config.get('volatility_threshold', 0.15)
        self.grid_recalc_bars = config.get('grid_recalc_bars', 20)
        self.grid_saturation_threshold = config.get('grid_saturation_threshold', 0.7)
        self.grid_boundary_margin_low = config.get('grid_boundary_margin_low', 0.98)
        self.grid_boundary_margin_high = config.get('grid_boundary_margin_high', 1.02)
        self.account_cap_pct = config.get('account_cap_pct', 0.05)
        self.active_grids = {}

    def calculate_price_range(self, df: pd.DataFrame) -> Dict:
        high = df['high'].max()
        low = df['low'].min()
        current_price = df['close'].iloc[-1]

        range_size = high - low
        range_pct = (range_size / current_price) * 100

        volatility = df['close'].pct_change().std() * np.sqrt(len(df))

        return {
            'high': high,
            'low': low,
            'current': current_price,
            'range_size': range_size,
            'range_pct': range_pct,
            'volatility': volatility,
            'is_ranging': range_pct < self.range_pct_threshold and volatility < self.volatility_threshold
        }

    def calculate_grid_levels(self, price_range: Dict) -> List[Tuple[str, float]]:
        current_price = price_range['current']
        grid_interval = current_price * (self.grid_spacing_pct / 100)

        grid_prices = []

        for i in range(self.grid_levels // 2):
            buy_price = current_price - (grid_interval * (i + 1))
            sell_price = current_price + (grid_interval * (i + 1))

            if buy_price > price_range['low'] * self.grid_boundary_margin_low:
                grid_prices.append(('buy', buy_price))
            if sell_price < price_range['high'] * self.grid_boundary_margin_high:
                grid_prices.append(('sell', sell_price))

        return sorted(grid_prices, key=lambda x: x[1])

    def generate_signals(self, coin: str) -> Optional[Dict]:
        try:
            candles = self._get_candles_or_none(coin, 50, lookback=self.range_period)
            if candles is None:
                return None

            df = candles
            price_range = self.calculate_price_range(df)

            logger.debug(
                f"Grid {coin}: price={price_range['current']:.2f} "
                f"range={price_range['range_pct']:.1f}% "
                f"vol={price_range['volatility']:.3f} "
                f"is_ranging={price_range['is_ranging']}"
            )

            if not price_range['is_ranging']:
                logger.info(
                    f"{coin} not in ranging market "
                    f"(range={price_range['range_pct']:.1f}%, "
                    f"vol={price_range['volatility']:.3f}), skipping grid strategy"
                )
                return None

            current_price = price_range['current']

            if coin not in self.active_grids:
                self.active_grids[coin] = {
                    'levels': self.calculate_grid_levels(price_range),
                    'filled_orders': {},
                    'last_update': df.index[-1]
                }

            grid_info = self.active_grids[coin]

            for order_type, grid_price in grid_info['levels']:
                price_key = f"{order_type}_{grid_price:.2f}"

                if price_key in grid_info['filled_orders']:
                    continue

                if order_type == 'buy' and current_price <= grid_price * 1.001:
                    if len(self.positions) < self.max_positions:
                        logger.info(f"Grid buy signal for {coin} at {grid_price:.2f}")
                        grid_info['filled_orders'][price_key] = True
                        return {
                            'side': 'buy',
                            'order_type': 'limit',
                            'post_only': True,
                            'confidence': 0.6,
                            'grid_price': grid_price
                        }

                elif order_type == 'sell' and current_price >= grid_price * 0.999:
                    if coin in self.positions and self.positions[coin]['size'] > 0:
                        logger.info(f"Grid sell signal for {coin} at {grid_price:.2f}")
                        grid_info['filled_orders'][price_key] = True
                        return {
                            'side': 'sell',
                            'order_type': 'limit',
                            'post_only': True,
                            'reduce_only': True,
                            'confidence': 0.6,
                            'grid_price': grid_price
                        }

            # Check if grid needs recalculation
            try:
                bars_since_update = (
                    len(candles) - candles.index.get_loc(grid_info['last_update'])
                )
            except KeyError:
                # Stored index no longer in new candles; force recalculation
                bars_since_update = self.grid_recalc_bars + 1

            if bars_since_update > self.grid_recalc_bars:
                self.active_grids[coin] = {
                    'levels': self.calculate_grid_levels(price_range),
                    'filled_orders': {},
                    'last_update': df.index[-1]
                }
                logger.info(f"Grid levels recalculated for {coin}")

            # Log grid status for observability
            if coin in self.active_grids:
                grid = self.active_grids[coin]
                total = len(grid['levels'])
                filled = len(grid['filled_orders'])
                logger.debug(
                    f"Grid {coin}: {filled}/{total} levels filled, "
                    f"price={current_price:.2f}"
                )

            return None

        except API_ERRORS as e:
            logger.error(f"Error generating grid signals for {coin}: {e}")
            return None

    def _coin_status(self, coin: str) -> str:
        """Grid-specific status: ranging state and fill count."""
        if coin not in self.active_grids:
            return "no_grid"
        grid = self.active_grids[coin]
        filled = len(grid['filled_orders'])
        total = len(grid['levels'])
        return f"grid:{filled}/{total}"

    def _calculate_limit_price(self, market_data, side: str, coin=None) -> float:
        if hasattr(self, '_current_signal') and 'grid_price' in self._current_signal:
            sz_dec, perp = self.market_data.price_rounding_params(coin) if coin is not None else (0, True)
            return round_price(self._current_signal['grid_price'], sz_dec, perp)
        return super()._calculate_limit_price(market_data, side, coin)

    def execute_signal(self, coin: str, signal: Dict):
        self._current_signal = signal
        super().execute_signal(coin, signal)
        self._current_signal = None

    def calculate_position_size(self, coin: str, signal: Dict) -> float:
        try:
            market_data = self.market_data.get_market_data(coin)
            if not market_data:
                return 0

            base_size_usd = self.position_size_per_grid
            if coin in self.active_grids:
                filled_count = len(self.active_grids[coin]['filled_orders'])
                if filled_count > self.grid_levels * self.grid_saturation_threshold:
                    base_size_usd *= 0.5

            position_size = self._apply_account_cap(base_size_usd, market_data.mid_price, cap_pct=self.account_cap_pct)

            logger.info(f"Grid position size for {coin}: {position_size}")
            return position_size

        except API_ERRORS as e:
            logger.error(f"Error calculating grid position size for {coin}: {e}")
            return 0
