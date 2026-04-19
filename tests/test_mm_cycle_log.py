"""Tests for MM strategy [cycle] log with inventory skew info."""

import logging
from collections import defaultdict
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(inventory_skew_bps=2, order_size_usd=100):
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)
    s.spread_bps = 10
    s.order_size_usd = order_size_usd
    s.max_open_orders = 4
    s.max_positions = 10
    s.maker_only = True
    s.close_immediately = False
    s.account_cap_pct = 0.25
    s.bbo_mode = False
    s.bbo_offset_bps = 0
    s.inventory_skew_bps = inventory_skew_bps
    s.inventory_skew_cap = 3.0
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
    s.imbalance_threshold = 0.0
    s.loss_streak_limit = 0
    s.loss_streak_cooldown = 300
    s._loss_streaks = defaultdict(int)
    s._coin_cooldown_until = {}
    s._quiet_hours = set()
    s._quiet_spread_multiplier = 0.0
    s._was_quiet = False
    s._max_coin_status_display = 10

    om = MagicMock()
    md = MagicMock()
    md.get_sz_decimals.return_value = 0
    md.price_rounding_params.return_value = (0, True)
    s.order_manager = om
    s.market_data = md

    tracker = MagicMock()
    tracker.get_order_count.return_value = 0
    tracker.active_coins.return_value = 0
    s._tracker = tracker

    closer = MagicMock()
    closer.tracked_coins = set()
    closer.get_close_oid.return_value = None
    s._closer = closer

    return s, om, md


class TestMMCycleLog:

    def test_idle_coin(self, caplog):
        s, om, md = _make_strategy()
        md.get_market_data.return_value = MagicMock(mid_price=100.0, bid=0, ask=0)
        md.round_size.return_value = 1.0
        om.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]

        with caplog.at_level(logging.INFO):
            s.run(['BTC'])

        cycle_lines = [r for r in caplog.records if '[cycle]' in r.message]
        assert len(cycle_lines) == 1
        assert 'BTC:idle' in cycle_lines[0].message

    def test_position_with_skew(self, caplog):
        s, om, md = _make_strategy(inventory_skew_bps=2, order_size_usd=100)
        s.positions = {'BTC': {'size': 1.0, 'entry_price': 100.0,
                               'unrealized_pnl': 0, 'margin_used': 10}}
        s.update_positions = MagicMock()  # prevent positions reset
        md.get_market_data.return_value = MagicMock(mid_price=100.0, bid=0, ask=0)

        with caplog.at_level(logging.INFO):
            s.run(['BTC'])

        cycle_lines = [r for r in caplog.records if '[cycle]' in r.message]
        assert len(cycle_lines) == 1
        assert 'BTC:skew+' in cycle_lines[0].message
        assert '1 pos' in cycle_lines[0].message

    def test_position_with_zero_skew(self, caplog):
        s, om, md = _make_strategy(inventory_skew_bps=0)
        s.positions = {'BTC': {'size': 1.0, 'entry_price': 100.0,
                               'unrealized_pnl': 0, 'margin_used': 10}}
        s.update_positions = MagicMock()
        md.get_market_data.return_value = MagicMock(mid_price=100.0, bid=0, ask=0)

        with caplog.at_level(logging.INFO):
            s.run(['BTC'])

        cycle_lines = [r for r in caplog.records if '[cycle]' in r.message]
        assert 'BTC:pos' in cycle_lines[0].message

    def test_market_data_none(self, caplog):
        s, om, md = _make_strategy(inventory_skew_bps=2)
        s.positions = {'BTC': {'size': 1.0, 'entry_price': 100.0,
                               'unrealized_pnl': 0, 'margin_used': 10}}
        s.update_positions = MagicMock()
        md.get_market_data.return_value = None

        with caplog.at_level(logging.INFO):
            s.run(['BTC'])

        cycle_lines = [r for r in caplog.records if '[cycle]' in r.message]
        # skew=0 when no market data → shows :pos
        assert 'BTC:pos' in cycle_lines[0].message

    def test_truncation(self, caplog):
        s, om, md = _make_strategy()
        s._max_coin_status_display = 3
        md.get_market_data.return_value = MagicMock(mid_price=100.0, bid=0, ask=0)
        md.round_size.return_value = 1.0
        om.bulk_place_orders.return_value = [MagicMock(id=1), MagicMock(id=2)]

        coins = [f'COIN{i}' for i in range(5)]
        with caplog.at_level(logging.INFO):
            s.run(coins)

        cycle_lines = [r for r in caplog.records if '[cycle]' in r.message]
        assert '... +2 more' in cycle_lines[0].message

    def test_active_position_count(self, caplog):
        s, om, md = _make_strategy(inventory_skew_bps=0)
        s.positions = {
            'BTC': {'size': 1.0, 'entry_price': 100.0,
                    'unrealized_pnl': 0, 'margin_used': 10},
            'ETH': {'size': -0.5, 'entry_price': 3000.0,
                    'unrealized_pnl': 0, 'margin_used': 30},
        }
        s.update_positions = MagicMock()
        md.get_market_data.return_value = MagicMock(mid_price=100.0, bid=0, ask=0)

        with caplog.at_level(logging.INFO):
            s.run(['BTC', 'ETH', 'SOL'])

        cycle_lines = [r for r in caplog.records if '[cycle]' in r.message]
        assert '2 pos' in cycle_lines[0].message
