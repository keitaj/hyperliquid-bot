"""Tests for volatility-adjusted BBO offset."""

from collections import defaultdict, deque
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy
from order_manager import round_price


def _make_strategy(bbo_mode=False, bbo_offset_bps=1.0, vol_adjust_enabled=False,
                   vol_adjust_multiplier=2.0, vol_lookback=30, vol_adjust_max_offset=50.0):
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)
    s.spread_bps = 10
    s.order_size_usd = 100
    s.max_open_orders = 4
    s.max_positions = 10
    s.maker_only = True
    s.account_cap_pct = 0.25
    s.bbo_mode = bbo_mode
    s.bbo_offset_bps = bbo_offset_bps
    s.inventory_skew_bps = 0
    s.inventory_skew_cap = 3.0
    s.imbalance_threshold = 0.0
    s.loss_streak_limit = 0
    s.loss_streak_cooldown = 300
    s._loss_streaks = defaultdict(int)
    s._coin_cooldown_until = {}
    s.vol_adjust_enabled = vol_adjust_enabled
    s.vol_adjust_multiplier = vol_adjust_multiplier
    s.vol_lookback = vol_lookback
    s.vol_adjust_max_offset = vol_adjust_max_offset
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
    s._max_coin_status_display = 10

    om = MagicMock()
    md = MagicMock()
    s.order_manager = om
    s.market_data = md

    tracker = MagicMock()
    tracker.get_order_count.return_value = 0
    s._tracker = tracker

    closer = MagicMock()
    closer.tracked_coins = set()
    s._closer = closer

    return s, om, md


class TestVolAdjustDisabled:

    def test_returns_base_offset_when_disabled(self):
        s, _, _ = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0, vol_adjust_enabled=False)
        result = s._get_volatility_adjusted_offset('BTC')
        assert result == 1.0

    def test_returns_base_offset_when_bbo_mode_off(self):
        s, _, _ = _make_strategy(bbo_mode=False, bbo_offset_bps=1.0, vol_adjust_enabled=True)
        result = s._get_volatility_adjusted_offset('BTC')
        assert result == 1.0


class TestVolAdjustInsufficientData:

    def test_returns_base_with_no_history(self):
        s, _, _ = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0, vol_adjust_enabled=True)
        result = s._get_volatility_adjusted_offset('BTC')
        assert result == 1.0

    def test_returns_base_with_four_points(self):
        s, _, _ = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0, vol_adjust_enabled=True)
        for price in [100.0, 100.1, 99.9, 100.05]:
            s._record_mid_price('BTC', price)
        result = s._get_volatility_adjusted_offset('BTC')
        assert result == 1.0


class TestVolAdjustCalculation:

    def test_stable_prices_minimal_adjustment(self):
        s, _, _ = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0,
                                 vol_adjust_enabled=True, vol_adjust_multiplier=2.0)
        for _ in range(6):
            s._record_mid_price('BTC', 100.0)
        result = s._get_volatility_adjusted_offset('BTC')
        assert result == 1.0

    def test_high_volatility_widens_offset(self):
        s, _, _ = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0,
                                 vol_adjust_enabled=True, vol_adjust_multiplier=2.0,
                                 vol_adjust_max_offset=500.0)  # high cap for this test
        prices = [100.0, 101.0, 100.0, 101.0, 100.0, 101.0]
        for p in prices:
            s._record_mid_price('BTC', p)
        result = s._get_volatility_adjusted_offset('BTC')
        assert result > 100

    def test_known_price_series(self):
        s, _, _ = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0,
                                 vol_adjust_enabled=True, vol_adjust_multiplier=2.0,
                                 vol_lookback=10)
        prices = [100.0, 100.10, 100.05, 100.20, 100.15, 100.25]
        for p in prices:
            s._record_mid_price('BTC', p)
        result = s._get_volatility_adjusted_offset('BTC')

        returns_bps = []
        for i in range(1, len(prices)):
            ret = abs(prices[i] - prices[i - 1]) / prices[i - 1] * 10_000
            returns_bps.append(ret)
        avg_move = sum(returns_bps) / len(returns_bps)
        expected = 1.0 + 2.0 * avg_move
        assert abs(result - expected) < 0.01

    def test_lookback_window_respected(self):
        s, _, _ = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0,
                                 vol_adjust_enabled=True, vol_adjust_multiplier=2.0,
                                 vol_lookback=6)
        volatile = [100.0, 105.0, 100.0, 105.0, 100.0, 105.0]
        stable = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
        for p in volatile:
            s._record_mid_price('BTC', p)
        for p in stable:
            s._record_mid_price('BTC', p)
        result = s._get_volatility_adjusted_offset('BTC')
        assert result == 1.0

    def test_per_coin_isolation(self):
        s, _, _ = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0,
                                 vol_adjust_enabled=True, vol_adjust_multiplier=2.0)
        volatile = [100.0, 105.0, 100.0, 105.0, 100.0, 105.0]
        stable = [200.0, 200.0, 200.0, 200.0, 200.0, 200.0]
        for p in volatile:
            s._record_mid_price('BTC', p)
        for p in stable:
            s._record_mid_price('ETH', p)
        eth_result = s._get_volatility_adjusted_offset('ETH')
        assert eth_result == 1.0

    def test_max_offset_cap(self):
        s, _, _ = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0,
                                 vol_adjust_enabled=True, vol_adjust_multiplier=2.0,
                                 vol_adjust_max_offset=20.0)
        # Extreme volatility: 10% moves
        prices = [100.0, 110.0, 100.0, 110.0, 100.0, 110.0]
        for p in prices:
            s._record_mid_price('BTC', p)
        result = s._get_volatility_adjusted_offset('BTC')
        assert result == 20.0  # capped

    def test_record_does_nothing_when_disabled(self):
        s, _, _ = _make_strategy(vol_adjust_enabled=False)
        s._record_mid_price('BTC', 100.0)
        assert 'BTC' not in s._recent_mids


class TestVolAdjustIntegration:

    def test_place_orders_uses_adjusted_offset(self):
        s, om, md = _make_strategy(bbo_mode=True, bbo_offset_bps=1.0,
                                   vol_adjust_enabled=True, vol_adjust_multiplier=2.0)
        # Pre-seed volatile mid history
        s._recent_mids['BTC'] = deque([100.0, 101.0, 100.0, 101.0, 100.0], maxlen=30)

        market_data = MagicMock()
        market_data.mid_price = 100.0
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
        buy_price = placed[0].price

        base_buy = round_price(99.99 * (1 - 1.0 / 10_000))
        assert buy_price < base_buy
