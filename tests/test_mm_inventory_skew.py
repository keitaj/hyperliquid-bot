"""Tests for inventory-based spread skewing."""

from collections import defaultdict
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy
from order_manager import round_price


def _make_strategy(inventory_skew_bps=2, bbo_mode=False, order_size_usd=100):
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)
    s.spread_bps = 10
    s.order_size_usd = order_size_usd
    s.max_open_orders = 4
    s.max_positions = 10
    s.maker_only = True
    s.account_cap_pct = 0.25
    s.bbo_mode = bbo_mode
    s.bbo_offset_bps = 1.0
    s.inventory_skew_bps = inventory_skew_bps
    s.inventory_skew_cap = 3.0
    s.positions = {}
    s._orders_placed = 0
    s._orders_placed_per_coin = defaultdict(int)
    s._fills_detected = 0
    s._fills_per_coin = defaultdict(int)
    s._fill_rate_log_interval = 300
    s._last_fill_rate_log = 0.0
    s._prev_position_coins = set()
    s._prev_positions = {}
    s.imbalance_threshold = 0.0
    s.loss_streak_limit = 0
    s.loss_streak_cooldown = 300
    s._loss_streaks = defaultdict(int)
    s._coin_cooldown_until = {}
    s._quiet_hours = set()
    s._quiet_spread_multiplier = 0.0
    s._was_quiet = False

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


class TestInventorySkewCalculation:

    def test_no_position_no_skew(self):
        s, _, _ = _make_strategy()
        assert s._calculate_inventory_skew('BTC', 100.0) == 0.0

    def test_long_positive_skew(self):
        s, _, _ = _make_strategy(inventory_skew_bps=2, order_size_usd=100)
        s.positions = {'BTC': {'size': 1.0}}  # 1 BTC @ $100 = $100 = 1x order_size
        skew = s._calculate_inventory_skew('BTC', 100.0)
        assert skew == 2.0  # 1.0 normalized * 2 bps

    def test_short_negative_skew(self):
        s, _, _ = _make_strategy(inventory_skew_bps=2, order_size_usd=100)
        s.positions = {'BTC': {'size': -1.0}}
        skew = s._calculate_inventory_skew('BTC', 100.0)
        assert skew == -2.0

    def test_skew_capped_at_3x(self):
        s, _, _ = _make_strategy(inventory_skew_bps=2, order_size_usd=100)
        s.positions = {'BTC': {'size': 10.0}}  # 10x order_size
        skew = s._calculate_inventory_skew('BTC', 100.0)
        assert skew == 6.0  # capped at 3.0 * 2 bps

    def test_skew_disabled_when_zero(self):
        s, _, _ = _make_strategy(inventory_skew_bps=0)
        s.positions = {'BTC': {'size': 1.0}}
        assert s._calculate_inventory_skew('BTC', 100.0) == 0.0

    def test_skew_scales_with_position(self):
        s, _, _ = _make_strategy(inventory_skew_bps=2, order_size_usd=100)
        s.positions = {'BTC': {'size': 0.5}}  # 0.5x order_size
        skew = s._calculate_inventory_skew('BTC', 100.0)
        assert skew == 1.0  # 0.5 * 2 bps


class TestInventorySkewOrderPlacement:

    def test_long_shifts_prices_down(self):
        s, om, md = _make_strategy(inventory_skew_bps=2, order_size_usd=100)
        s.positions = {'BTC': {'size': 1.0}}  # long 1 BTC

        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 0
        market_data.ask = 0
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []

        def capture(orders):
            placed.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]
        om.bulk_place_orders.side_effect = capture

        # Get prices without skew for comparison
        raw_buy, raw_sell = s._get_spread_prices(100.0)
        no_skew_buy, no_skew_sell = round_price(raw_buy), round_price(raw_sell)

        s._place_orders('BTC')

        buy_order = placed[0]
        sell_order = placed[1]
        # Long → positive skew → prices shift DOWN
        assert buy_order.price < no_skew_buy  # buy cheaper
        assert sell_order.price < no_skew_sell  # sell cheaper (more attractive)

    def test_short_shifts_prices_up(self):
        s, om, md = _make_strategy(inventory_skew_bps=2, order_size_usd=100)
        s.positions = {'BTC': {'size': -1.0}}  # short 1 BTC

        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 0
        market_data.ask = 0
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []

        def capture(orders):
            placed.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]
        om.bulk_place_orders.side_effect = capture

        raw_buy, raw_sell = s._get_spread_prices(100.0)
        no_skew_buy, no_skew_sell = round_price(raw_buy), round_price(raw_sell)

        s._place_orders('BTC')

        buy_order = placed[0]
        sell_order = placed[1]
        # Short → negative skew → prices shift UP
        assert buy_order.price > no_skew_buy  # buy more expensive (more attractive)
        assert sell_order.price > no_skew_sell  # sell more expensive

    def test_no_position_no_shift(self):
        s, om, md = _make_strategy(inventory_skew_bps=2)

        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 0
        market_data.ask = 0
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []

        def capture(orders):
            placed.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]
        om.bulk_place_orders.side_effect = capture

        raw_buy, raw_sell = s._get_spread_prices(100.0)
        no_skew_buy, no_skew_sell = round_price(raw_buy), round_price(raw_sell)

        s._place_orders('BTC')

        buy_order = placed[0]
        sell_order = placed[1]
        assert buy_order.price == no_skew_buy
        assert sell_order.price == no_skew_sell
