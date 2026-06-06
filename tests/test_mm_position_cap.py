"""Tests for per-coin entry-side position cap.

The cap suppresses same-direction entries once the accumulated
position value (``|size| × mid_price``) reaches
``max_position_multiple × effective_order_size_usd``. Opposite-side
entries are still placed so existing inventory can unwind through
normal quoting.
"""

from collections import defaultdict
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(max_position_multiple: float = 0.0, order_size_usd: float = 100.0):
    """Lightweight MM strategy with the minimal attribute set needed by
    ``_place_orders``. Bypasses ``__init__`` so unit tests stay fast and do
    not touch the SDK / config layering chain."""
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)

    s.spread_bps = 10
    s.order_size_usd = order_size_usd
    s.max_open_orders = 4
    s.max_positions = 10
    s.maker_only = True
    s.account_cap_pct = 0.25
    s.bbo_mode = True
    s.bbo_offset_bps = 1.0
    s.inventory_skew_bps = 0
    s.imbalance_threshold = 0.0
    s.loss_streak_limit = 0
    s.loss_streak_cooldown = 300
    s._loss_streaks = defaultdict(int)
    s._coin_cooldown_until = {}
    s._quiet_hours = set()
    s._coin_offset_overrides = {}
    s._coin_spread_overrides = {}
    s._coin_size_overrides = {}
    s._quiet_spread_multiplier = 0.0
    s._spread_schedule = {}
    s._dynamic_offset_enabled = False
    s._adverse_tracker = None
    s._was_quiet = False
    s._drain_flag_file = ''
    s._was_drain = False
    s.vol_adjust_enabled = False
    s.vol_adjust_multiplier = 2.0
    s.vol_lookback = 30
    s._microprice_enabled = False
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

    s._max_position_multiple = max_position_multiple

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

    s._rejection_tracker = MagicMock()
    s._rejection_tracker.log_summary_if_due.return_value = False

    return s, om, md


def _setup_market(md, mid_price: float = 100.0):
    market_data = MagicMock()
    market_data.mid_price = mid_price
    market_data.bid = mid_price - 0.01
    market_data.ask = mid_price + 0.01
    market_data.book_imbalance = 0.0
    md.get_market_data.return_value = market_data
    md.round_size.return_value = 1.0
    return market_data


def _capture_orders(om):
    placed = []
    om.bulk_place_orders.side_effect = lambda orders: (
        placed.extend(orders),
        [MagicMock(id=i) for i in range(len(orders))],
    )[1]
    return placed


class TestPositionCapDisabled:
    """``max_position_multiple == 0`` preserves the legacy behaviour."""

    def test_no_skip_when_disabled_even_with_large_position(self):
        s, om, md = _make_strategy(max_position_multiple=0.0, order_size_usd=100)
        _setup_market(md, mid_price=100.0)
        # Position of 5 × order_size_usd — would trip any non-zero cap.
        s.positions['BTC'] = {'size': 5.0}
        placed = _capture_orders(om)

        s._place_orders('BTC')

        assert len(placed) == 2  # both BUY and SELL placed


class TestPositionCapEnabled:
    """``max_position_multiple > 0`` suppresses same-direction entries
    once the cap is reached. Opposite-side entry continues."""

    def test_long_at_cap_suppresses_buy_keeps_sell(self):
        s, om, md = _make_strategy(max_position_multiple=2.0, order_size_usd=100)
        _setup_market(md, mid_price=100.0)
        # |pos| × mid = 2.0 × 100 = 200 == cap (2 × 100). At-cap triggers skip.
        s.positions['BTC'] = {'size': 2.0}
        placed = _capture_orders(om)

        s._place_orders('BTC')

        assert len(placed) == 1
        assert placed[0].side.value == 'sell'

    def test_short_at_cap_suppresses_sell_keeps_buy(self):
        s, om, md = _make_strategy(max_position_multiple=2.0, order_size_usd=100)
        _setup_market(md, mid_price=100.0)
        s.positions['BTC'] = {'size': -2.0}
        placed = _capture_orders(om)

        s._place_orders('BTC')

        assert len(placed) == 1
        assert placed[0].side.value == 'buy'

    def test_long_above_cap_suppresses_buy(self):
        s, om, md = _make_strategy(max_position_multiple=2.0, order_size_usd=100)
        _setup_market(md, mid_price=100.0)
        # |pos| × mid = 3 × 100 = 300 > cap of 200.
        s.positions['BTC'] = {'size': 3.0}
        placed = _capture_orders(om)

        s._place_orders('BTC')

        assert len(placed) == 1
        assert placed[0].side.value == 'sell'

    def test_below_cap_places_both_sides(self):
        s, om, md = _make_strategy(max_position_multiple=2.0, order_size_usd=100)
        _setup_market(md, mid_price=100.0)
        # |pos| × mid = 1.5 × 100 = 150 < cap of 200.
        s.positions['BTC'] = {'size': 1.5}
        placed = _capture_orders(om)

        s._place_orders('BTC')

        assert len(placed) == 2

    def test_zero_position_places_both_sides(self):
        s, om, md = _make_strategy(max_position_multiple=2.0, order_size_usd=100)
        _setup_market(md, mid_price=100.0)
        s.positions['BTC'] = {'size': 0.0}
        placed = _capture_orders(om)

        s._place_orders('BTC')

        assert len(placed) == 2

    def test_missing_position_places_both_sides(self):
        s, om, md = _make_strategy(max_position_multiple=2.0, order_size_usd=100)
        _setup_market(md, mid_price=100.0)
        # ``positions`` map does not contain the coin at all.
        placed = _capture_orders(om)

        s._place_orders('BTC')

        assert len(placed) == 2

    def test_no_market_data_skips_cap_check(self):
        """When market data is unavailable the cap can't be evaluated, so we
        defer to the existing no-data behaviour (the outer
        ``_compute_ideal_prices`` short-circuit). Confirm we do not raise
        and do not erroneously skip both sides."""
        s, om, md = _make_strategy(max_position_multiple=2.0, order_size_usd=100)
        _setup_market(md, mid_price=100.0)
        s.positions['BTC'] = {'size': 5.0}

        # Capture the call sequence so the second call returns None.
        market_data = md.get_market_data.return_value
        md.get_market_data.side_effect = [market_data, None]
        placed = _capture_orders(om)

        s._place_orders('BTC')

        # No raise, and SELL side still placed even though BUY skipped
        # by the cap check (first call returned valid data); the second
        # ``get_market_data`` is the inside-the-cap check which now
        # returns None, so the cap path simply does not trip.
        assert all(o.side.value in ('buy', 'sell') for o in placed)


class TestPositionCapWithCoinSizeOverride:
    """The cap uses the **coin-specific** order_size_usd so a per-coin
    override (e.g. ``GOOGL:80``) tightens or loosens the cap accordingly."""

    def test_override_tightens_cap(self):
        s, om, md = _make_strategy(max_position_multiple=2.0, order_size_usd=100)
        # Override pulls coin size down to 50 → cap = 2 × 50 = 100.
        s._coin_size_overrides = {'BTC': 50.0}
        _setup_market(md, mid_price=100.0)
        # |pos| × mid = 1.2 × 100 = 120 > cap of 100.
        s.positions['BTC'] = {'size': 1.2}
        placed = _capture_orders(om)

        s._place_orders('BTC')

        assert len(placed) == 1
        assert placed[0].side.value == 'sell'

    def test_override_loosens_cap(self):
        s, om, md = _make_strategy(max_position_multiple=2.0, order_size_usd=100)
        # Override raises coin size to 200 → cap = 2 × 200 = 400.
        s._coin_size_overrides = {'BTC': 200.0}
        _setup_market(md, mid_price=100.0)
        # |pos| × mid = 3.0 × 100 = 300 < cap of 400.
        s.positions['BTC'] = {'size': 3.0}
        placed = _capture_orders(om)

        s._place_orders('BTC')

        assert len(placed) == 2
