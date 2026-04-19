"""Tests for adverse selection guards: L2 book imbalance and loss streak cooldown."""

import time
from collections import defaultdict
from unittest.mock import MagicMock, patch

import pytest

from strategies.market_making_strategy import MarketMakingStrategy
from ttl_cache import TTLCacheMap


def _make_strategy(imbalance_threshold=0.0, loss_streak_limit=0, loss_streak_cooldown=300):
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)
    s.spread_bps = 10
    s.order_size_usd = 100
    s.max_open_orders = 4
    s.max_positions = 10
    s.maker_only = True
    s.account_cap_pct = 0.25
    s.bbo_mode = True
    s.bbo_offset_bps = 1.0
    s.inventory_skew_bps = 0
    s.imbalance_threshold = imbalance_threshold
    s.loss_streak_limit = loss_streak_limit
    s.loss_streak_cooldown = loss_streak_cooldown
    s._loss_streaks = defaultdict(int)
    s._coin_cooldown_until = {}
    s._quiet_hours = set()
    s._quiet_spread_multiplier = 0.0
    s._was_quiet = False
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
    closer.get_close_oid.return_value = None
    s._closer = closer

    return s, om, md


class TestBookImbalanceGuard:
    """L2 book imbalance skips the unfavorable side."""

    def test_no_skip_when_disabled(self):
        s, om, md = _make_strategy(imbalance_threshold=0.0)
        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.99
        market_data.ask = 100.01
        market_data.book_imbalance = -0.8  # heavy sell pressure
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []
        om.bulk_place_orders.side_effect = lambda orders: (
            placed.extend(orders),
            [MagicMock(id=i) for i in range(len(orders))]
        )[1]

        s._place_orders('BTC')
        assert len(placed) == 2  # both sides placed

    def test_skip_buy_on_sell_pressure(self):
        s, om, md = _make_strategy(imbalance_threshold=0.4)
        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.99
        market_data.ask = 100.01
        market_data.book_imbalance = -0.5  # ask-heavy → skip buy
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []
        om.bulk_place_orders.side_effect = lambda orders: (
            placed.extend(orders),
            [MagicMock(id=i) for i in range(len(orders))]
        )[1]

        s._place_orders('BTC')
        assert len(placed) == 1
        assert placed[0].side.value == 'sell'

    def test_skip_sell_on_buy_pressure(self):
        s, om, md = _make_strategy(imbalance_threshold=0.4)
        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.99
        market_data.ask = 100.01
        market_data.book_imbalance = 0.6  # bid-heavy → skip sell
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []
        om.bulk_place_orders.side_effect = lambda orders: (
            placed.extend(orders),
            [MagicMock(id=i) for i in range(len(orders))]
        )[1]

        s._place_orders('BTC')
        assert len(placed) == 1
        assert placed[0].side.value == 'buy'

    def test_no_skip_below_threshold(self):
        s, om, md = _make_strategy(imbalance_threshold=0.4)
        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.99
        market_data.ask = 100.01
        market_data.book_imbalance = 0.3  # below threshold
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        placed = []
        om.bulk_place_orders.side_effect = lambda orders: (
            placed.extend(orders),
            [MagicMock(id=i) for i in range(len(orders))]
        )[1]

        s._place_orders('BTC')
        assert len(placed) == 2


class TestLossStreakCooldown:
    """Per-coin loss streak triggers a cooldown period."""

    def test_cooldown_after_streak(self):
        s, om, md = _make_strategy(loss_streak_limit=2, loss_streak_cooldown=60)

        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.99
        market_data.ask = 100.01
        market_data.book_imbalance = 0.0
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        om.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]

        # Simulate 2 consecutive losing closes
        # Cycle 1: BTC gets a position (bought at 100, now at 99.5 = loss)
        s.positions = {'BTC': {'size': 1.0, 'entryPx': 100.0}}
        s._prev_position_coins = {'BTC'}
        s._prev_positions = {'BTC': {'size': 1.0, 'entryPx': 100.0}}

        # Market data shows price below entry → loss
        market_data.mid_price = 99.5

        # Cycle 2: BTC position closed (loss)
        s.positions = {}
        s.update_positions = MagicMock()
        s.run(['BTC'])
        assert s._loss_streaks['BTC'] == 1

        # Cycle 3: BTC gets another position
        s._prev_position_coins = {'BTC'}
        s._prev_positions = {'BTC': {'size': 1.0, 'entryPx': 100.0}}
        s.positions = {}

        # Cycle 4: closed again (loss) → triggers cooldown
        s.run(['BTC'])
        assert s._loss_streaks['BTC'] == 2
        assert 'BTC' in s._coin_cooldown_until

    def test_streak_resets_on_win(self):
        s, om, md = _make_strategy(loss_streak_limit=2, loss_streak_cooldown=60)

        # 1 loss
        s._loss_streaks['BTC'] = 1
        s.positions = {}
        s._prev_position_coins = {'BTC'}
        s._prev_positions = {'BTC': {'size': 1.0, 'entryPx': 99.0}}  # bought at 99

        s.update_positions = MagicMock()
        md.get_market_data.return_value = MagicMock(
            mid_price=100, bid=99.99, ask=100.01, book_imbalance=0.0
        )
        md.round_size.return_value = 1.0
        om.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]

        s.run(['BTC'])
        assert s._loss_streaks['BTC'] == 0  # reset

    def test_cooldown_blocks_orders(self):
        s, om, md = _make_strategy(loss_streak_limit=2, loss_streak_cooldown=300)

        # Set coin in cooldown
        s._coin_cooldown_until['BTC'] = time.monotonic() + 300

        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.99
        market_data.ask = 100.01
        market_data.book_imbalance = 0.0
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        s.positions = {}
        s.update_positions = MagicMock()

        s.run(['BTC'])
        om.bulk_place_orders.assert_not_called()

    def test_cooldown_expires(self):
        s, om, md = _make_strategy(loss_streak_limit=2, loss_streak_cooldown=0.01)

        # Set coin in cooldown that's already expired
        s._coin_cooldown_until['BTC'] = time.monotonic() - 1
        s._loss_streaks['BTC'] = 2

        market_data = MagicMock()
        market_data.mid_price = 100.0
        market_data.bid = 99.99
        market_data.ask = 100.01
        market_data.book_imbalance = 0.0
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0

        om.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]
        s._tracker.active_coins.return_value = 0

        s.positions = {}
        s.update_positions = MagicMock()

        s.run(['BTC'])
        om.bulk_place_orders.assert_called_once()
        assert s._loss_streaks['BTC'] == 0  # reset after cooldown


class TestBookImbalanceInMarketData:
    """MarketData.book_imbalance is computed from L2 snapshot."""

    def test_balanced_book(self):
        from market_data import MarketDataManager
        mdm = MagicMock(spec=MarketDataManager)
        mdm.info = MagicMock()

        # Symmetric book
        l2 = {'levels': [
            [{'px': '99.99', 'sz': '10'}, {'px': '99.98', 'sz': '10'}],
            [{'px': '100.01', 'sz': '10'}, {'px': '100.02', 'sz': '10'}],
        ]}

        # Call the real method
        real_mdm = MarketDataManager.__new__(MarketDataManager)
        real_mdm._cache = TTLCacheMap(ttl=2.0)
        real_mdm._cache_ttl = 2.0
        real_mdm._imbalance_depth = 5

        with patch.object(type(real_mdm), 'get_l2_snapshot', return_value=l2):
            md = real_mdm.get_market_data('BTC')

        assert md is not None
        assert abs(md.book_imbalance) < 0.01  # balanced

    def test_bid_heavy_book(self):
        from market_data import MarketDataManager
        real_mdm = MarketDataManager.__new__(MarketDataManager)
        real_mdm._cache = TTLCacheMap(ttl=2.0)
        real_mdm._cache_ttl = 2.0
        real_mdm._imbalance_depth = 5

        l2 = {'levels': [
            [{'px': '99.99', 'sz': '100'}, {'px': '99.98', 'sz': '100'}],
            [{'px': '100.01', 'sz': '10'}, {'px': '100.02', 'sz': '10'}],
        ]}

        with patch.object(type(real_mdm), 'get_l2_snapshot', return_value=l2):
            md = real_mdm.get_market_data('BTC')

        assert md.book_imbalance > 0.8  # bid-heavy

    def test_ask_heavy_book(self):
        from market_data import MarketDataManager
        real_mdm = MarketDataManager.__new__(MarketDataManager)
        real_mdm._cache = TTLCacheMap(ttl=2.0)
        real_mdm._cache_ttl = 2.0
        real_mdm._imbalance_depth = 5

        l2 = {'levels': [
            [{'px': '99.99', 'sz': '10'}],
            [{'px': '100.01', 'sz': '100'}],
        ]}

        with patch.object(type(real_mdm), 'get_l2_snapshot', return_value=l2):
            md = real_mdm.get_market_data('BTC')

        assert md.book_imbalance < -0.8  # ask-heavy


class TestInputValidation:
    """Config validation catches invalid parameters."""

    def _init_strategy(self, **overrides):
        config = {'spread_bps': 10, 'order_size_usd': 100}
        config.update(overrides)
        md = MagicMock()
        om = MagicMock()
        return MarketMakingStrategy(md, om, config)

    def test_imbalance_threshold_negative(self):
        with pytest.raises(ValueError, match="imbalance_threshold"):
            self._init_strategy(imbalance_threshold=-0.1)

    def test_imbalance_threshold_above_one(self):
        with pytest.raises(ValueError, match="imbalance_threshold"):
            self._init_strategy(imbalance_threshold=1.5)

    def test_loss_streak_limit_negative(self):
        with pytest.raises(ValueError, match="loss_streak_limit"):
            self._init_strategy(loss_streak_limit=-1)

    def test_loss_streak_cooldown_zero_with_limit(self):
        with pytest.raises(ValueError, match="loss_streak_cooldown"):
            self._init_strategy(loss_streak_limit=2, loss_streak_cooldown=0)
