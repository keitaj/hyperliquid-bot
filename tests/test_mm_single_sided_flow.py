"""Tests pinning the single-sided quoting invariant.

``MarketMakingStrategy.run()`` hands any coin that holds a position to
``PositionCloser`` and ``continue``s, so ``_place_orders()`` only ever runs
while flat. Two documented parameters -- ``inventory_skew_bps`` and
``max_position_multiple`` -- gate on a *non-zero* position and are therefore
inert in production, which is why ``__init__`` warns when either is set.

The tests below pin that invariant. If a future change introduces two-sided
quoting (a coin keeps quoting while holding inventory), ``test_place_orders_*``
will fail -- that is intentional: the startup warnings, the README note and the
``_calculate_inventory_skew`` docstring must be updated in the same change.
"""

from collections import defaultdict
from unittest.mock import MagicMock, patch

import pytest

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy():
    """MM strategy with the minimal attribute set needed to drive ``run()``.

    Bypasses ``__init__`` so the test stays fast and does not touch the SDK /
    config layering chain.
    """
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **k: None):
        s = MarketMakingStrategy.__new__(MarketMakingStrategy)

    s.spread_bps = 10
    s.order_size_usd = 100.0
    s.max_open_orders = 4
    s.max_positions = 10
    s.maker_only = True
    s.bbo_mode = True
    s.bbo_offset_bps = 1.0
    s.close_immediately = False
    s.inventory_skew_bps = 0
    s.inventory_skew_cap = 3.0
    s.imbalance_threshold = 0.0
    s.loss_streak_limit = 0
    s.loss_streak_cooldown = 300
    s.refresh_tolerance_bp = 0
    s._loss_streaks = defaultdict(int)
    s._coin_cooldown_until = {}
    s._quiet_hours = set()
    s._quiet_spread_multiplier = 0.0
    s._spread_schedule = {}
    s._coin_offset_overrides = {}
    s._coin_spread_overrides = {}
    s._coin_size_overrides = {}
    s._dynamic_offset_enabled = False
    s._adverse_tracker = None
    s._coin_health_tracker = None
    s._was_quiet = False
    s._drain_flag_file = ''
    s._was_drain = False
    s.vol_adjust_enabled = False
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
    s._max_position_multiple = 0.0

    s.order_manager = MagicMock()
    md = MagicMock()
    md.get_sz_decimals.return_value = 0
    md.price_rounding_params.return_value = (0, True)
    s.market_data = md

    tracker = MagicMock()
    tracker.get_order_count.return_value = 0
    tracker.active_coins.return_value = 0
    s._tracker = tracker
    s._closer = MagicMock()
    s._closer.tracked_coins = set()
    s._rejection_tracker = MagicMock()

    # Isolate the branch under test: everything the loop calls around the
    # position check is stubbed so only the routing decision is exercised.
    s.update_positions = MagicMock()
    s._log_fill_rate = MagicMock()
    s._log_dynamic_age = MagicMock()
    s._get_dynamic_position_age = MagicMock(return_value=None)
    s._compute_ideal_prices = MagicMock(return_value=None)
    s._place_orders = MagicMock()
    return s


class TestSingleSidedFlowInvariant:
    """``_place_orders`` must never run for a coin that holds a position."""

    def test_place_orders_skipped_while_position_open(self):
        s = _make_strategy()
        s.positions = {'BTC': {'size': 1.0, 'entryPx': 100.0}}

        s.run(['BTC'])

        s._place_orders.assert_not_called()
        s._closer.manage.assert_called_once()

    def test_place_orders_runs_when_flat(self):
        s = _make_strategy()
        s.positions = {}

        s.run(['BTC'])

        s._place_orders.assert_called_once()
        s._closer.manage.assert_not_called()

    def test_only_flat_coins_are_quoted_in_a_mixed_cycle(self):
        """With one coin holding and one flat, only the flat coin quotes."""
        s = _make_strategy()
        s.positions = {'BTC': {'size': 1.0, 'entryPx': 100.0}}

        s.run(['BTC', 'ETH'])

        quoted = [c.args[0] for c in s._place_orders.call_args_list]
        assert quoted == ['ETH']


class TestNoOpDisclosureWarnings:
    """Non-zero inert parameters must announce themselves at startup."""

    def _init_with(self, inventory_skew_bps: float, max_position_multiple: float, caplog):
        """Run only the disclosure block against a stub instance.

        ``__init__`` builds the whole config chain, so the block is exercised
        directly with the same inputs it reads.
        """
        s = _make_strategy()
        s.inventory_skew_bps = inventory_skew_bps
        s._max_position_multiple = max_position_multiple
        with caplog.at_level('WARNING'):
            MarketMakingStrategy._warn_inert_parameters(s)
        return caplog.text

    def test_warns_when_inventory_skew_set(self, caplog):
        text = self._init_with(2.0, 0.0, caplog)
        assert 'inventory_skew_bps' in text
        assert 'NO EFFECT' in text

    def test_warns_when_position_cap_set(self, caplog):
        text = self._init_with(0, 2.5, caplog)
        assert 'max_position_multiple' in text
        assert 'NO EFFECT' in text

    def test_silent_at_defaults(self, caplog):
        text = self._init_with(0, 0.0, caplog)
        assert 'NO EFFECT' not in text


class TestCycleLogOmitsSkew:
    """The cycle log must not print a skew that cannot reach an order."""

    def test_position_coin_logs_pos_not_skew(self, caplog):
        s = _make_strategy()
        s.inventory_skew_bps = 2.0  # would produce a non-zero skew if surfaced
        s.positions = {'BTC': {'size': 1.0, 'entryPx': 100.0}}

        with caplog.at_level('INFO'):
            s.run(['BTC'])

        cycle_lines = [r.message for r in caplog.records if '[cycle]' in r.message]
        assert cycle_lines, 'expected a [cycle] log line'
        assert 'BTC:pos' in cycle_lines[0]
        assert 'skew' not in cycle_lines[0]


class TestInventorySkewStillZeroWhenFlat:
    """The guard that makes the feature inert is itself pinned."""

    @pytest.mark.parametrize('positions', [{}, {'BTC': {'size': 0.0}}])
    def test_returns_zero_without_position(self, positions):
        s = _make_strategy()
        s.inventory_skew_bps = 5.0
        s.positions = positions

        assert s._calculate_inventory_skew('BTC', 100.0) == 0.0
