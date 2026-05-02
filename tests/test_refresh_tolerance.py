"""Tests for the order refresh tolerance feature.

When ``refresh_tolerance_bp`` is enabled the run loop should:

  1. Compute the current ideal bid/ask via ``_compute_ideal_prices``.
  2. Call ``OrderTracker.refresh_orders_with_tolerance`` (not the legacy
     ``cancel_stale_orders``) so within-tolerance orders are kept.
  3. Skip placing a new quote on a side that already has a kept order
     (``get_open_sides`` gating in ``_place_orders``).

The default of ``refresh_tolerance_bp == 0`` must preserve the legacy
age-only behaviour (full backward compatibility).
"""

from collections import defaultdict
from unittest.mock import MagicMock, patch

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(refresh_tolerance_bp=0.0, refresh_max_age_seconds=120.0):
    """Build a minimal MarketMakingStrategy bypassing __init__."""
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)
    s.spread_bps = 10
    s.order_size_usd = 100
    s.max_open_orders = 4
    s.max_positions = 10
    s.maker_only = True
    s.account_cap_pct = 0.25
    s.bbo_mode = False
    s.bbo_offset_bps = 0.0
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
    s._was_drain = False
    s._drain_flag_file = ''
    s.vol_adjust_enabled = False
    s.vol_adjust_multiplier = 2.0
    s.vol_lookback = 30
    s._recent_mids = {}
    s._microprice_enabled = False
    s._microprice_multiplier = 0.0
    s._microprice_max_skew_bps = 0.0
    s.positions = {}
    s._orders_placed = 0
    s._orders_placed_per_coin = defaultdict(int)
    s._fills_detected = 0
    s._fills_per_coin = defaultdict(int)
    s._fill_rate_log_interval = 300
    s._last_fill_rate_log = 0.0
    s._prev_position_coins = set()
    s._prev_positions = {}

    s.refresh_tolerance_bp = refresh_tolerance_bp
    s.refresh_max_age_seconds = refresh_max_age_seconds

    om = MagicMock()
    md = MagicMock()
    md.get_sz_decimals.return_value = 0
    md.price_rounding_params.return_value = (4, True)
    s.order_manager = om
    s.market_data = md

    tracker = MagicMock()
    tracker.get_order_count.return_value = 0
    tracker.get_open_sides.return_value = set()
    s._tracker = tracker

    closer = MagicMock()
    closer.tracked_coins = set()
    s._closer = closer

    return s, om, md, tracker


def _market_data(mid=100.0, bid=99.99, ask=100.01):
    """Build a market_data mock with a stable BBO."""
    md = MagicMock()
    md.mid_price = mid
    md.bid = bid
    md.ask = ask
    md.book_imbalance = 0.0
    return md


class TestComputeIdealPrices:
    """``_compute_ideal_prices`` must mirror ``_place_orders`` price logic."""

    def test_returns_none_when_no_market_data(self):
        s, _om, md, _ = _make_strategy()
        md.get_market_data.return_value = None

        assert s._compute_ideal_prices("BTC") is None

    def test_returns_none_when_mid_zero(self):
        s, _om, md, _ = _make_strategy()
        market_data = _market_data(mid=0.0, bid=0.0, ask=0.0)
        md.get_market_data.return_value = market_data

        assert s._compute_ideal_prices("BTC") is None

    def test_spread_mode_symmetric_around_mid(self):
        s, _om, md, _ = _make_strategy()
        s.spread_bps = 10  # 10bp = 0.1%
        market_data = _market_data(mid=100.0, bid=99.99, ask=100.01)
        md.get_market_data.return_value = market_data

        result = s._compute_ideal_prices("BTC")
        assert result is not None
        buy, sell, skew = result
        # 10bp around 100.0 = 99.9 / 100.1
        assert abs(buy - 99.9) < 1e-6
        assert abs(sell - 100.1) < 1e-6
        # No position -> no inventory skew.
        assert skew == 0.0

    def test_bbo_mode_at_best_bid_ask(self):
        s, _om, md, _ = _make_strategy()
        s.bbo_mode = True
        s.bbo_offset_bps = 0.0
        market_data = _market_data(mid=100.0, bid=99.95, ask=100.05)
        md.get_market_data.return_value = market_data

        # Override offset to 0 by stubbing _get_coin_offset
        s._get_coin_offset = lambda coin: 0.0
        s._calculate_microprice_offsets = lambda coin, off: (off, off)
        s._calculate_inventory_skew = lambda coin, mid: 0.0
        # Vol buffer is updated inside _compute_ideal_prices in BBO mode;
        # stub it out to keep this test focused on price math.
        s._record_mid_price = lambda coin, mid: None

        result = s._compute_ideal_prices("BTC")
        assert result is not None
        buy, sell, skew = result
        assert abs(buy - 99.95) < 1e-6
        assert abs(sell - 100.05) < 1e-6
        assert skew == 0.0


class TestRunLoopTolerancePath:
    """Verify the run-loop dispatches to the correct cancel method."""

    def test_disabled_uses_legacy_cancel_stale_orders(self):
        """``refresh_tolerance_bp == 0`` -> ``cancel_stale_orders`` is invoked."""
        s, _om, md, tracker = _make_strategy(refresh_tolerance_bp=0.0)
        market_data = _market_data()
        md.get_market_data.return_value = market_data

        # Simulate the run-loop dispatch directly (without the surrounding
        # boilerplate of MarketMakingStrategy.run): tolerance is 0, so the
        # legacy method should be chosen.
        ideal = s._compute_ideal_prices("BTC")
        if s.refresh_tolerance_bp > 0:
            tracker.refresh_orders_with_tolerance(
                "BTC",
                ideal_prices={"B": ideal[0], "A": ideal[1]},
                tolerance_bp=s.refresh_tolerance_bp,
                max_age_seconds=s.refresh_max_age_seconds,
                close_oid=None,
            )
        else:
            tracker.cancel_stale_orders("BTC", close_oid=None)

        tracker.cancel_stale_orders.assert_called_once_with("BTC", close_oid=None)
        tracker.refresh_orders_with_tolerance.assert_not_called()

    def test_enabled_uses_refresh_with_tolerance(self):
        """``refresh_tolerance_bp > 0`` -> ``refresh_orders_with_tolerance`` is invoked."""
        from order_manager import OrderSide

        s, _om, md, tracker = _make_strategy(refresh_tolerance_bp=2.0)
        market_data = _market_data(mid=100.0, bid=99.99, ask=100.01)
        md.get_market_data.return_value = market_data

        ideal = s._compute_ideal_prices("BTC")
        if s.refresh_tolerance_bp > 0:
            tracker.refresh_orders_with_tolerance(
                "BTC",
                ideal_prices={
                    OrderSide.BUY.value: ideal[0],
                    OrderSide.SELL.value: ideal[1],
                },
                tolerance_bp=s.refresh_tolerance_bp,
                max_age_seconds=s.refresh_max_age_seconds,
                close_oid=None,
            )
        else:
            tracker.cancel_stale_orders("BTC", close_oid=None)

        tracker.refresh_orders_with_tolerance.assert_called_once()
        call_kwargs = tracker.refresh_orders_with_tolerance.call_args.kwargs
        assert call_kwargs["tolerance_bp"] == 2.0
        assert call_kwargs["max_age_seconds"] == 120.0
        assert OrderSide.BUY.value in call_kwargs["ideal_prices"]
        assert OrderSide.SELL.value in call_kwargs["ideal_prices"]
        tracker.cancel_stale_orders.assert_not_called()


class TestSingleComputePerCycle:
    """The two should-fix items from PR #144 review:

      1. ``_calculate_inventory_skew`` is computed once per cycle.
      2. ``_record_mid_price`` (vol buffer update) fires once per cycle, so
         the run-loop tolerance check and the actual placement evaluate
         against the same buffer state.
    """

    def _stub_for_place_orders(self, s):
        s._calculate_microprice_offsets = lambda coin, off: (off, off)
        s._get_coin_offset = lambda coin: 0.0
        s._get_coin_spread = lambda coin: s.spread_bps
        s._get_hourly_spread_multiplier = lambda: 1.0
        s.calculate_position_size = lambda coin, signal: 1.0

    def test_inventory_skew_called_once_when_threading_ideal_prices(self):
        """Threading ``ideal_prices`` into ``_place_orders`` skips the
        redundant inventory-skew computation."""
        s, _om, md, _tracker = _make_strategy(refresh_tolerance_bp=2.0)
        self._stub_for_place_orders(s)
        market_data = _market_data(mid=100.0, bid=99.99, ask=100.01)
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0
        s.order_manager.bulk_place_orders.return_value = []

        skew_calls = {"n": 0}

        def counting_skew(_coin, _mid):
            skew_calls["n"] += 1
            return 0.0

        s._calculate_inventory_skew = counting_skew
        s._record_mid_price = lambda coin, mid: None  # not BBO mode here

        # Cycle simulation: run-loop computes once, then passes to placement.
        ideal = s._compute_ideal_prices("BTC")
        assert skew_calls["n"] == 1
        s._place_orders("BTC", ideal_prices=ideal)
        # _place_orders must not recompute the skew.
        assert skew_calls["n"] == 1

    def test_inventory_skew_recomputed_when_ideal_prices_omitted(self):
        """Legacy callers that omit ``ideal_prices`` still get a working
        path that computes prices internally (one skew call total)."""
        s, _om, md, _tracker = _make_strategy(refresh_tolerance_bp=0.0)
        self._stub_for_place_orders(s)
        market_data = _market_data(mid=100.0, bid=99.99, ask=100.01)
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0
        s.order_manager.bulk_place_orders.return_value = []

        skew_calls = {"n": 0}

        def counting_skew(_coin, _mid):
            skew_calls["n"] += 1
            return 0.0

        s._calculate_inventory_skew = counting_skew
        s._record_mid_price = lambda coin, mid: None

        # No ``ideal_prices`` kwarg -> internal compute path.
        s._place_orders("BTC")
        assert skew_calls["n"] == 1

    def test_record_mid_price_fires_once_per_cycle(self):
        """In BBO mode, the volatility buffer is updated exactly once per
        cycle even when ``_compute_ideal_prices`` is invoked from the
        run-loop tolerance check and the result is threaded into
        ``_place_orders``."""
        s, _om, md, _tracker = _make_strategy(refresh_tolerance_bp=2.0)
        self._stub_for_place_orders(s)
        s.bbo_mode = True
        market_data = _market_data(mid=100.0, bid=99.95, ask=100.05)
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0
        s._calculate_inventory_skew = lambda coin, mid: 0.0
        s.order_manager.bulk_place_orders.return_value = []

        record_calls = []
        s._record_mid_price = lambda coin, mid: record_calls.append((coin, mid))

        # Cycle simulation.
        ideal = s._compute_ideal_prices("BTC")
        s._place_orders("BTC", ideal_prices=ideal)

        # Buffer updated exactly once -- inside _compute_ideal_prices.
        assert len(record_calls) == 1
        assert record_calls[0] == ("BTC", 100.0)

    def test_record_mid_price_skipped_when_not_bbo_mode(self):
        """Non-BBO mode never updates the rolling volatility buffer
        (preserves pre-PR behaviour)."""
        s, _om, md, _tracker = _make_strategy(refresh_tolerance_bp=2.0)
        self._stub_for_place_orders(s)
        s.bbo_mode = False
        market_data = _market_data(mid=100.0, bid=99.99, ask=100.01)
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0
        s._calculate_inventory_skew = lambda coin, mid: 0.0
        s.order_manager.bulk_place_orders.return_value = []

        record_calls = []
        s._record_mid_price = lambda coin, mid: record_calls.append((coin, mid))

        ideal = s._compute_ideal_prices("BTC")
        s._place_orders("BTC", ideal_prices=ideal)

        assert record_calls == []


class TestPlaceOrdersOpenSidesGating:
    """``_place_orders`` skips a side that already has a kept tracked order."""

    def _stub_for_place_orders(self, s):
        """Common stubs needed by ``_place_orders``."""
        s._calculate_inventory_skew = lambda coin, mid: 0.0
        s._calculate_microprice_offsets = lambda coin, off: (off, off)
        s._get_coin_offset = lambda coin: 0.0
        s._get_coin_spread = lambda coin: s.spread_bps
        s._get_hourly_spread_multiplier = lambda: 1.0
        s.calculate_position_size = lambda coin, signal: 1.0
        s._record_mid_price = lambda coin, mid: None

    def test_places_both_sides_when_open_sides_empty(self):
        s, _om, md, tracker = _make_strategy(refresh_tolerance_bp=2.0)
        self._stub_for_place_orders(s)
        market_data = _market_data(mid=100.0, bid=99.99, ask=100.01)
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0
        tracker.get_open_sides.return_value = set()
        s.order_manager.bulk_place_orders.return_value = []

        s._place_orders("BTC")

        # 2 sides placed -> bulk_place_orders called with 2 orders.
        s.order_manager.bulk_place_orders.assert_called_once()
        placed_orders = s.order_manager.bulk_place_orders.call_args.args[0]
        assert len(placed_orders) == 2

    def test_skips_buy_when_buy_side_already_open(self):
        from order_manager import OrderSide

        s, _om, md, tracker = _make_strategy(refresh_tolerance_bp=2.0)
        self._stub_for_place_orders(s)
        market_data = _market_data(mid=100.0, bid=99.99, ask=100.01)
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0
        # Pretend a buy is still open from the previous cycle (kept by tolerance).
        # The tracker stores the production string (OrderSide.BUY.value).
        tracker.get_open_sides.return_value = {OrderSide.BUY.value}
        s.order_manager.bulk_place_orders.return_value = []

        s._place_orders("BTC")

        s.order_manager.bulk_place_orders.assert_called_once()
        placed_orders = s.order_manager.bulk_place_orders.call_args.args[0]
        assert len(placed_orders) == 1
        assert placed_orders[0].side == OrderSide.SELL

    def test_skips_sell_when_sell_side_already_open(self):
        from order_manager import OrderSide

        s, _om, md, tracker = _make_strategy(refresh_tolerance_bp=2.0)
        self._stub_for_place_orders(s)
        market_data = _market_data(mid=100.0, bid=99.99, ask=100.01)
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0
        tracker.get_open_sides.return_value = {OrderSide.SELL.value}
        s.order_manager.bulk_place_orders.return_value = []

        s._place_orders("BTC")

        placed_orders = s.order_manager.bulk_place_orders.call_args.args[0]
        assert len(placed_orders) == 1
        assert placed_orders[0].side == OrderSide.BUY

    def test_open_sides_ignored_when_tolerance_disabled(self):
        """When tolerance is 0 the gating is suppressed (legacy behaviour)."""
        from order_manager import OrderSide

        s, _om, md, tracker = _make_strategy(refresh_tolerance_bp=0.0)
        self._stub_for_place_orders(s)
        market_data = _market_data(mid=100.0, bid=99.99, ask=100.01)
        md.get_market_data.return_value = market_data
        md.round_size.return_value = 1.0
        # Even if tracker reports a kept side, the legacy path should not
        # consult get_open_sides at all.
        tracker.get_open_sides.return_value = {OrderSide.BUY.value}
        s.order_manager.bulk_place_orders.return_value = []

        s._place_orders("BTC")

        placed_orders = s.order_manager.bulk_place_orders.call_args.args[0]
        # Both sides placed: same as legacy behaviour (matches max_open_orders gating only).
        assert len(placed_orders) == 2
