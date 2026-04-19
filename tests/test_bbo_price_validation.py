"""Tests for BBO price validation in maker-only mode."""

import time
from collections import defaultdict
from unittest.mock import MagicMock, patch

from strategies.mm_position_closer import PositionCloser
from strategies.market_making_strategy import MarketMakingStrategy
from order_manager import BBO_OFFSET, OrderSide, round_price


def _make_closer(maker_only=True, spread_bps=10, max_age=120):
    om = MagicMock()
    md = MagicMock()
    md.round_size.return_value = 0.5
    md.get_sz_decimals.return_value = 0
    md.price_rounding_params.return_value = (0, True)
    closer = PositionCloser(
        order_manager=om,
        market_data=md,
        spread_bps=spread_bps,
        max_position_age_seconds=max_age,
        maker_only=maker_only,
        taker_fallback_age_seconds=None,
    )
    om.get_all_positions.return_value = [{'coin': 'BTC', 'szi': '1.0'}]
    return closer, om, md


class TestTakeProfitBBOClamping:
    """_place_take_profit clamps price to BBO instead of placing inside spread."""

    def test_sell_clamped_above_ask(self):
        closer, om, md = _make_closer(spread_bps=5)
        # ask is above the take-profit price
        market_data = MagicMock()
        market_data.bid = 100.00
        market_data.ask = 100.02
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic()
        # entry at 100.01, spread 5bps → take-profit sell at 100.06
        # but ask is 100.02, so 100.06 > ask → no clamping needed
        closer._place_take_profit('BTC', 1.0, 100.01, entry_time, 0)
        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        assert call_kwargs['price'] > market_data.ask

    def test_sell_clamped_when_crossing(self):
        closer, om, md = _make_closer(spread_bps=1)
        # Very tight spread: take-profit would land inside ask
        market_data = MagicMock()
        market_data.bid = 100.00
        market_data.ask = 100.05
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic()
        # entry at 100.00, spread 1bps → take-profit sell at ~100.01
        # ask is 100.05, so 100.01 < ask → must clamp to ask + offset
        closer._place_take_profit('BTC', 1.0, 100.00, entry_time, 0)
        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        assert call_kwargs['price'] > market_data.ask

    def test_buy_clamped_when_crossing(self):
        closer, om, md = _make_closer(spread_bps=1)
        market_data = MagicMock()
        market_data.bid = 99.95
        market_data.ask = 100.00
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic()
        # short entry at 100.00, spread 1bps → take-profit buy at ~99.99
        # bid is 99.95, so 99.99 > bid → must clamp to bid - offset
        closer._place_take_profit('BTC', -1.0, 100.00, entry_time, 0)
        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        assert call_kwargs['price'] < market_data.bid


class TestForceCloseBBOPricing:
    """_handle_force_close uses BBO-aware pricing."""

    def test_sell_at_ask_plus_offset(self):
        closer, om, md = _make_closer()
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 99.99
        market_data.ask = 100.01
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic() - 200
        closer._open_positions['BTC'] = (entry_time, None)
        closer._handle_force_close('BTC', 1.0, 200, entry_time, None, MagicMock())

        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        expected = round_price(100.01 * (1 + BBO_OFFSET))
        assert call_kwargs['price'] == expected
        assert call_kwargs['side'] == OrderSide.SELL

    def test_buy_at_bid_minus_offset(self):
        closer, om, md = _make_closer()
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 99.99
        market_data.ask = 100.01
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic() - 200
        closer._open_positions['BTC'] = (entry_time, None)
        closer._handle_force_close('BTC', -1.0, 200, entry_time, None, MagicMock())

        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        expected = round_price(99.99 * (1 - BBO_OFFSET))
        assert call_kwargs['price'] == expected
        assert call_kwargs['side'] == OrderSide.BUY

    def test_skips_when_no_bbo(self):
        closer, om, md = _make_closer()
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 0
        market_data.ask = 0
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic() - 200
        closer._handle_force_close('BTC', 1.0, 200, entry_time, None, MagicMock())

        om.create_limit_order.assert_not_called()


class TestPlaceOrdersBBOClamping:
    """_place_orders clamps buy/sell prices outside BBO when maker_only=True."""

    def _make_strategy(self, maker_only=True, spread_bps=5):
        with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
            s = MarketMakingStrategy.__new__(MarketMakingStrategy)
        s.spread_bps = spread_bps
        s.order_size_usd = 100
        s.max_open_orders = 4
        s.max_positions = 10
        s.maker_only = maker_only
        s.account_cap_pct = 0.25
        s.bbo_mode = False
        s.bbo_offset_bps = 0
        s.inventory_skew_bps = 0
        s.imbalance_threshold = 0.0
        s.loss_streak_limit = 0
        s.loss_streak_cooldown = 300
        s._loss_streaks = defaultdict(int)
        s._coin_cooldown_until = {}
        s._quiet_hours = set()
        s._coin_offset_overrides = {}
        s._coin_spread_overrides = {}
        s._quiet_spread_multiplier = 0.0
        s._was_quiet = False
        s.positions = {}
        s._orders_placed = 0
        s._orders_placed_per_coin = defaultdict(int)
        s._fills_detected = 0
        s._fills_per_coin = defaultdict(int)
        s._fill_rate_log_interval = 300
        s._last_fill_rate_log = 0.0
        s._prev_position_coins = set()
        s._prev_positions = {}

        om = MagicMock()
        md = MagicMock()
        md.get_sz_decimals.return_value = 0
        md.price_rounding_params.return_value = (0, True)
        s.order_manager = om
        s.market_data = md

        tracker = MagicMock()
        tracker.get_order_count.return_value = 0
        s._tracker = tracker

        closer = MagicMock()
        closer.tracked_coins = set()
        s._closer = closer

        return s, om, md

    def test_buy_clamped_below_bid_on_tight_spread(self):
        s, om, md = self._make_strategy(spread_bps=1)
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 100.00  # tight: buy_price from spread would be ~99.99
        market_data.ask = 100.01
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        # Mock bulk_place_orders to capture the orders
        placed_orders = []

        def capture_orders(orders):
            placed_orders.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]
        om.bulk_place_orders.side_effect = capture_orders

        s._place_orders('BTC')

        assert len(placed_orders) >= 1
        buy_order = placed_orders[0]
        assert buy_order.price < market_data.bid

    def test_no_clamping_when_not_maker_only(self):
        s, om, md = self._make_strategy(maker_only=False, spread_bps=1)
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 100.00
        market_data.ask = 100.01
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed_orders = []

        def capture_orders(orders):
            placed_orders.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]
        om.bulk_place_orders.side_effect = capture_orders

        s._place_orders('BTC')

        assert len(placed_orders) >= 1
        # Without clamping, buy_price = mid - spread = 99.99 which is < bid
        # so no clamping needed, price stays as spread-calculated
