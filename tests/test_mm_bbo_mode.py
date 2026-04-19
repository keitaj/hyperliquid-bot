"""Tests for BBO-following order placement mode."""

from collections import defaultdict
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy
from order_manager import round_price


def _make_strategy(bbo_mode=False, bbo_offset_bps=0, spread_bps=10, maker_only=True):
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)
    s.spread_bps = spread_bps
    s.order_size_usd = 100
    s.max_open_orders = 4
    s.max_positions = 10
    s.maker_only = maker_only
    s.account_cap_pct = 0.25
    s.bbo_mode = bbo_mode
    s.bbo_offset_bps = bbo_offset_bps
    s.inventory_skew_bps = 0
    s.imbalance_threshold = 0.0
    s.loss_streak_limit = 0
    s.loss_streak_cooldown = 300
    s._loss_streaks = defaultdict(int)
    s._coin_cooldown_until = {}
    s.vol_adjust_enabled = False
    s.vol_adjust_multiplier = 2.0
    s.vol_lookback = 30
    s._recent_mids = {}
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


class TestBBOModeOrderPlacement:
    """BBO mode places orders at best bid/ask."""

    def test_buy_at_bid_sell_at_ask(self):
        s, om, md = _make_strategy(bbo_mode=True)
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 99.99
        market_data.ask = 100.01
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []

        def capture(orders):
            placed.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]
        om.bulk_place_orders.side_effect = capture

        s._place_orders('BTC')

        assert len(placed) >= 2
        buy_order = placed[0]
        sell_order = placed[1]
        assert buy_order.price == round_price(99.99)
        assert sell_order.price == round_price(100.01)

    def test_bbo_with_offset(self):
        s, om, md = _make_strategy(bbo_mode=True, bbo_offset_bps=1)
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 99.99
        market_data.ask = 100.01
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []

        def capture(orders):
            placed.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]
        om.bulk_place_orders.side_effect = capture

        s._place_orders('BTC')

        buy_order = placed[0]
        sell_order = placed[1]
        # 1bp offset: buy below bid, sell above ask
        assert buy_order.price < 99.99
        assert sell_order.price > 100.01

    def test_fallback_when_no_bbo(self):
        s, om, md = _make_strategy(bbo_mode=True, spread_bps=5)
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 0
        market_data.ask = 0
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []

        def capture(orders):
            placed.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]
        om.bulk_place_orders.side_effect = capture

        s._place_orders('BTC')

        # Falls back to mid±spread
        buy_order = placed[0]
        sell_order = placed[1]
        expected_buy = round_price(100.00 * (1 - 5 / 10_000))
        expected_sell = round_price(100.00 * (1 + 5 / 10_000))
        assert buy_order.price == expected_buy
        assert sell_order.price == expected_sell

    def test_non_bbo_mode_unchanged(self):
        s, om, md = _make_strategy(bbo_mode=False, spread_bps=10)
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 99.99
        market_data.ask = 100.01
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []

        def capture(orders):
            placed.extend(orders)
            return [MagicMock(id=i) for i in range(len(orders))]
        om.bulk_place_orders.side_effect = capture

        s._place_orders('BTC')

        buy_order = placed[0]
        # Non-BBO: mid - 10bps = 99.90, not at bid 99.99
        assert buy_order.price < 99.95
