"""Tests for PositionCloser -- aging close tiers.

Tests the progressive close tightening: positions that age past 50%
and 75% of max_position_age get their close orders repriced at breakeven
and small-loss levels respectively, reducing taker force-closes.
"""

import time
from unittest.mock import MagicMock

import pytest

from strategies.mm_position_closer import (
    PositionCloser,
    _TIER_AGGRESSIVE,
    _TIER_BREAKEVEN,
    _TIER_NORMAL,
)


def _make_closer(max_age=120, maker_only=True, taker_fallback=None,
                 spread_bps=10):
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
        taker_fallback_age_seconds=taker_fallback,
    )
    om.get_all_positions.return_value = [{'coin': 'BTC', 'szi': '1.0'}]
    return closer, om, md


class TestGetTier:
    """_get_tier returns correct tier based on position age."""

    def test_normal_tier_early(self):
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier('BTC', 0) == _TIER_NORMAL
        assert closer._get_tier('BTC', 30) == _TIER_NORMAL
        assert closer._get_tier('BTC', 59) == _TIER_NORMAL

    def test_breakeven_tier_at_50pct(self):
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier('BTC', 60) == _TIER_BREAKEVEN
        assert closer._get_tier('BTC', 80) == _TIER_BREAKEVEN
        assert closer._get_tier('BTC', 89) == _TIER_BREAKEVEN

    def test_aggressive_tier_at_75pct(self):
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier('BTC', 90) == _TIER_AGGRESSIVE
        assert closer._get_tier('BTC', 110) == _TIER_AGGRESSIVE
        assert closer._get_tier('BTC', 119) == _TIER_AGGRESSIVE


class TestTierSpreadBps:
    """_tier_spread_bps returns correct spread for each tier."""

    def test_normal_tier_uses_configured_spread(self):
        closer, _, _ = _make_closer(spread_bps=10)
        assert closer._tier_spread_bps(_TIER_NORMAL) == 10

    def test_breakeven_tier_zero_spread(self):
        closer, _, _ = _make_closer(spread_bps=10)
        assert closer._tier_spread_bps(_TIER_BREAKEVEN) == 0.0

    def test_aggressive_tier_negative_spread(self):
        closer, _, _ = _make_closer(spread_bps=10)
        assert closer._tier_spread_bps(_TIER_AGGRESSIVE) == -1.0


class TestTightenClose:
    """manage() cancels and re-places close order when tier transitions."""

    def test_tightens_at_breakeven_threshold(self):
        """When position ages past 50%, close order should be cancelled
        and re-placed at breakeven."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        # Position placed 65s ago, currently at TIER_NORMAL with order
        entry_time = time.monotonic() - 65
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_NORMAL)

        # Close order is still alive
        om.get_open_orders.return_value = [{'oid': 42}]

        mock_order = MagicMock()
        mock_order.id = 99
        om.create_limit_order.return_value = mock_order

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should have cancelled old order
        om.cancel_order.assert_called_once_with(42, 'BTC')
        # Should have placed new order
        om.create_limit_order.assert_called_once()
        # New order should be tracked at BREAKEVEN tier
        assert closer._open_positions['BTC'][2] == _TIER_BREAKEVEN

    def test_tightens_at_aggressive_threshold(self):
        """When position ages past 75%, close order should be cancelled
        and re-placed at loss-cut price."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        # Position placed 95s ago, at TIER_BREAKEVEN with close order
        entry_time = time.monotonic() - 95
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_BREAKEVEN)

        # Close order is still alive
        om.get_open_orders.return_value = [{'oid': 42}]

        mock_order = MagicMock()
        mock_order.id = 99
        om.create_limit_order.return_value = mock_order

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should have cancelled old order
        om.cancel_order.assert_called_once_with(42, 'BTC')
        # Should have placed new order
        om.create_limit_order.assert_called_once()
        # New order should be tracked at AGGRESSIVE tier
        assert closer._open_positions['BTC'][2] == _TIER_AGGRESSIVE

    def test_no_tighten_when_already_at_correct_tier(self):
        """When already at BREAKEVEN tier and still in breakeven range,
        should NOT cancel and re-place."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        # Position at 65s, already at BREAKEVEN tier
        entry_time = time.monotonic() - 65
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_BREAKEVEN)

        # Close order is still alive
        om.get_open_orders.return_value = [{'oid': 42}]

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should NOT have cancelled or placed any order
        om.cancel_order.assert_not_called()
        om.create_limit_order.assert_not_called()

    def test_no_tighten_in_normal_period(self):
        """During normal period (< 50% age), should not tighten."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        # Position at 30s, at NORMAL tier
        entry_time = time.monotonic() - 30
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_NORMAL)

        # Close order is still alive
        om.get_open_orders.return_value = [{'oid': 42}]

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should NOT have cancelled or placed any order
        om.cancel_order.assert_not_called()
        om.create_limit_order.assert_not_called()

    def test_skips_tier_if_cancel_fails(self):
        """If cancel fails, should return without placing new order."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        entry_time = time.monotonic() - 65
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_NORMAL)

        om.get_open_orders.return_value = [{'oid': 42}]
        om.cancel_order.side_effect = ConnectionError("API error")

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should NOT have placed any new order
        om.create_limit_order.assert_not_called()


class TestClosePriceCalculation:
    """Close price reflects the tier spread correctly."""

    def test_normal_tier_long_close_price(self):
        """For a long at normal tier, close price = entry * (1 + spread)."""
        closer, om, md = _make_closer(max_age=120, spread_bps=10, maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic()  # just now, age ~0
        closer._open_positions['BTC'] = (entry_time, None, 0)
        closer._place_take_profit('BTC', 0.5, 50000.0, entry_time, _TIER_NORMAL)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # 50000 * (1 + 10/10000) = 50050
        assert abs(call_kwargs['price'] - 50050.0) < 1.0

    def test_breakeven_tier_long_close_price(self):
        """For a long at breakeven tier, close price = entry."""
        closer, om, md = _make_closer(max_age=120, spread_bps=10, maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic() - 65  # past 50%
        closer._open_positions['BTC'] = (entry_time, None, 0)
        closer._place_take_profit('BTC', 0.5, 50000.0, entry_time, _TIER_BREAKEVEN)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # 50000 * (1 + 0/10000) = 50000
        assert abs(call_kwargs['price'] - 50000.0) < 1.0

    def test_aggressive_tier_long_close_below_entry(self):
        """For a long at aggressive tier, close price < entry."""
        closer, om, md = _make_closer(max_age=120, spread_bps=10, maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic() - 95  # past 75%
        closer._open_positions['BTC'] = (entry_time, None, 0)
        closer._place_take_profit('BTC', 0.5, 50000.0, entry_time, _TIER_AGGRESSIVE)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # 50000 * (1 + (-1)/10000) = 50000 * 0.9999 = 49995
        assert call_kwargs['price'] < 50000.0

    def test_aggressive_tier_short_close_above_entry(self):
        """For a short at aggressive tier, close price > entry."""
        closer, om, md = _make_closer(max_age=120, spread_bps=10, maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic() - 95
        closer._open_positions['BTC'] = (entry_time, None, 0)
        closer._place_take_profit('BTC', -0.5, 50000.0, entry_time, _TIER_AGGRESSIVE)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # 50000 * (1 - (-1)/10000) = 50000 * 1.0001 = 50005
        assert call_kwargs['price'] > 50000.0


class TestManageBasicBehavior:
    """Basic manage() behavior is preserved."""

    def test_new_position_tracked_at_normal_tier(self):
        """New positions start at TIER_NORMAL."""
        closer, om, md = _make_closer(maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        assert 'BTC' in closer._open_positions
        assert closer._open_positions['BTC'][2] == _TIER_NORMAL

    def test_force_close_at_max_age(self):
        """Taker force-close fires at max_position_age."""
        closer, om, _ = _make_closer(max_age=60, maker_only=False)

        entry_time = time.monotonic() - 120  # well past max_age
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 50000.0}

        closer.manage('BTC', position, close_fn)

        close_fn.assert_called_once_with('BTC')
        assert 'BTC' not in closer._open_positions

    def test_force_close_maker_tracks_aggressive_tier(self):
        """Maker-only force-close stores AGGRESSIVE tier."""
        closer, om, md = _make_closer(max_age=60, maker_only=True)

        entry_time = time.monotonic() - 120
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        md_obj = MagicMock()
        md_obj.mid_price = 50000.0
        md_obj.bid = 49999.0
        md_obj.ask = 50001.0
        md.get_market_data.return_value = md_obj

        mock_order = MagicMock()
        mock_order.id = 99
        om.create_limit_order.return_value = mock_order

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 50000.0}

        closer.manage('BTC', position, close_fn)

        # Should not have used taker (maker_only=True, no taker_fallback)
        close_fn.assert_not_called()
        # Should have stored AGGRESSIVE tier
        assert closer._open_positions['BTC'][2] == _TIER_AGGRESSIVE


# ------------------------------------------------------------------ #
#  Per-coin close tier overrides + toxicity acceleration
# ------------------------------------------------------------------ #


class _StubTracker:
    """Minimal AdverseSelectionTracker stand-in (stats + recent windows)."""

    def __init__(self, stats=None, recent=None):
        self._stats = stats or {}
        self._recent = recent or {}

    @property
    def stats(self):
        return self._stats

    def get_recent_windows(self, coin, n):
        return self._recent.get(coin, [])[-n:]


def _make_tier_closer(max_age=120, coin_tiers=None, tox_cfg=None, tracker=None,
                      min_seconds=0.0, breakeven=0.50, aggressive=0.75,
                      maker_only=False):
    om = MagicMock()
    md = MagicMock()
    md.round_size.return_value = 0.5
    md.price_rounding_params.return_value = (0, True)
    om.get_all_positions.return_value = [{'coin': 'BTC', 'szi': '1.0'}]
    closer = PositionCloser(
        order_manager=om,
        market_data=md,
        spread_bps=10,
        max_position_age_seconds=max_age,
        maker_only=maker_only,
        taker_fallback_age_seconds=None,
        close_breakeven_pct=breakeven,
        close_aggressive_pct=aggressive,
        coin_close_tier_overrides=coin_tiers,
        close_tier_toxicity=tox_cfg,
        close_tier_min_seconds=min_seconds,
    )
    if tracker is not None:
        closer.set_adverse_tracker(tracker)
    return closer, om, md


class TestGetTierPctsForCoin:
    """Per-coin tier override resolution: full name -> bare name -> global."""

    def test_no_overrides_returns_global(self):
        closer, _, _ = _make_tier_closer()
        assert closer._get_tier_pcts_for_coin('xyz:NVDA') == (0.50, 0.75)

    def test_full_name_hit(self):
        closer, _, _ = _make_tier_closer(coin_tiers={'xyz:NVDA': (0.30, 0.55)})
        assert closer._get_tier_pcts_for_coin('xyz:NVDA') == (0.30, 0.55)

    def test_bare_name_fallback(self):
        closer, _, _ = _make_tier_closer(coin_tiers={'NVDA': (0.30, 0.55)})
        assert closer._get_tier_pcts_for_coin('xyz:NVDA') == (0.30, 0.55)

    def test_full_name_preferred_over_bare(self):
        closer, _, _ = _make_tier_closer(
            coin_tiers={'NVDA': (0.30, 0.55), 'xyz:NVDA': (0.40, 0.65)})
        assert closer._get_tier_pcts_for_coin('xyz:NVDA') == (0.40, 0.65)

    def test_other_coin_uses_global(self):
        closer, _, _ = _make_tier_closer(coin_tiers={'NVDA': (0.30, 0.55)})
        assert closer._get_tier_pcts_for_coin('xyz:TSLA') == (0.50, 0.75)

    def test_relaxing_override_allowed(self):
        # Overrides can also be later than global (relaxation)
        closer, _, _ = _make_tier_closer(coin_tiers={'XYZ100': (0.45, 0.70)})
        assert closer._get_tier_pcts_for_coin('xyz:XYZ100') == (0.45, 0.70)


class TestToxicityAccel:
    """_apply_toxicity_accel firing conditions and fail-safe fallbacks."""

    CFG = dict(enabled=True, threshold_bps=-2.0, window='30s',
               multiplier=0.6, min_fills=5, floor_pct=0.15)

    def _cfg(self, **kw):
        from strategies.mm_config import CloseTierToxicityConfig
        return CloseTierToxicityConfig(**{**self.CFG, **kw})

    def test_fires_below_threshold(self):
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -3.0}})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == pytest.approx((0.30, 0.45))

    def test_fires_at_threshold_exactly(self):
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -2.0}})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == pytest.approx((0.30, 0.45))

    def test_no_fire_above_threshold(self):
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -1.0}})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == (0.50, 0.75)

    def test_floor_pct_applied(self):
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -5.0}})
        closer, _, _ = _make_tier_closer(
            tox_cfg=self._cfg(multiplier=0.1), tracker=tracker)
        eff_b, eff_a = closer._apply_toxicity_accel('BTC', 0.50, 0.75)
        # 0.50 * 0.1 = 0.05 < floor 0.15 -> floored
        assert eff_b == 0.15
        assert eff_a >= eff_b

    def test_low_fills_falls_back_to_recent_window(self):
        tracker = _StubTracker(
            stats={'BTC': {'fills': 2, 'avg_30s': -5.0}},
            recent={'BTC': [{'ts': time.time(), 'fills': 12, 'avg_30s': -3.0}]})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == pytest.approx((0.30, 0.45))

    def test_recent_window_also_low_fills_no_fire(self):
        tracker = _StubTracker(
            stats={'BTC': {'fills': 2, 'avg_30s': -5.0}},
            recent={'BTC': [{'ts': time.time(), 'fills': 3, 'avg_30s': -5.0}]})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == (0.50, 0.75)

    def test_stale_recent_window_no_fire(self):
        # Idle coin: last completed window is an hour old -> must not fire
        tracker = _StubTracker(
            stats={'BTC': {'fills': 0}},
            recent={'BTC': [{'ts': time.time() - 3600, 'fills': 12, 'avg_30s': -5.0}]})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == (0.50, 0.75)

    def test_recent_window_missing_ts_no_fire(self):
        # Snapshot without a timestamp is treated as stale (safe side)
        tracker = _StubTracker(
            recent={'BTC': [{'fills': 12, 'avg_30s': -5.0}]})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == (0.50, 0.75)

    def test_floor_never_raises_above_base(self):
        # Pure-scratch override (b_pct=0.0): toxicity must not push the
        # breakeven threshold above the base while flow is toxic
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -5.0}})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        eff_b, eff_a = closer._apply_toxicity_accel('BTC', 0.0, 0.55)
        assert eff_b == 0.0
        assert eff_a == pytest.approx(0.33)

    def test_floor_clamped_to_base_when_base_below_floor(self):
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -5.0}})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        # base b_pct 0.10 < floor 0.15: effective floor is the base itself
        eff_b, eff_a = closer._apply_toxicity_accel('BTC', 0.10, 0.20)
        assert eff_b <= 0.10
        assert eff_a <= 0.20
        assert eff_b == pytest.approx(0.10)  # max(0.06, min(0.15, 0.10))
        assert eff_a == pytest.approx(0.12)

    def test_no_history_no_fire(self):
        tracker = _StubTracker()
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == (0.50, 0.75)

    def test_missing_sample_label_no_fire(self):
        # Fills present but the 30s samples have not matured yet
        tracker = _StubTracker(stats={'BTC': {'fills': 10}})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == (0.50, 0.75)

    def test_tracker_none_passthrough(self):
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg())
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == (0.50, 0.75)

    def test_disabled_passthrough_even_with_tracker(self):
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -5.0}})
        closer, _, _ = _make_tier_closer(
            tox_cfg=self._cfg(enabled=False), tracker=tracker)
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == (0.50, 0.75)

    def test_window_selection_60s(self):
        tracker = _StubTracker(
            stats={'BTC': {'fills': 10, 'avg_30s': -5.0, 'avg_60s': -1.0}})
        closer, _, _ = _make_tier_closer(
            tox_cfg=self._cfg(window='60s'), tracker=tracker)
        # avg_60s (-1.0) is above threshold -> no fire despite avg_30s
        assert closer._apply_toxicity_accel('BTC', 0.50, 0.75) == (0.50, 0.75)

    def test_state_transition_logged_once(self, caplog):
        import logging
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -3.0}})
        closer, _, _ = _make_tier_closer(tox_cfg=self._cfg(), tracker=tracker)
        with caplog.at_level(logging.INFO, logger='strategies.mm_position_closer'):
            closer._apply_toxicity_accel('BTC', 0.50, 0.75)   # OFF -> ON
            closer._apply_toxicity_accel('BTC', 0.50, 0.75)   # steady ON
            on_lines = [r for r in caplog.records if 'accel ON' in r.message]
            assert len(on_lines) == 1
            assert 'avg_30s=-3.0bps' in on_lines[0].message

            tracker._stats['BTC']['avg_30s'] = 0.5
            closer._apply_toxicity_accel('BTC', 0.50, 0.75)   # ON -> OFF
            closer._apply_toxicity_accel('BTC', 0.50, 0.75)   # steady OFF
            off_lines = [r for r in caplog.records if 'accel OFF' in r.message]
            assert len(off_lines) == 1


class TestGetTierWithOverridesAndFloors:
    """_get_tier combining overrides, toxicity, min_seconds, max_age_override."""

    def test_override_shifts_thresholds_earlier(self):
        closer, _, _ = _make_tier_closer(
            max_age=120, coin_tiers={'BTC': (0.25, 0.50)})
        assert closer._get_tier('BTC', 29) == _TIER_NORMAL
        assert closer._get_tier('BTC', 30) == _TIER_BREAKEVEN
        assert closer._get_tier('BTC', 59) == _TIER_BREAKEVEN
        assert closer._get_tier('BTC', 60) == _TIER_AGGRESSIVE

    def test_override_only_affects_that_coin(self):
        closer, _, _ = _make_tier_closer(
            max_age=120, coin_tiers={'BTC': (0.25, 0.50)})
        assert closer._get_tier('ETH', 30) == _TIER_NORMAL
        assert closer._get_tier('ETH', 60) == _TIER_BREAKEVEN

    def test_golden_regression_defaults(self):
        # Default config must match the legacy _get_tier exactly
        closer, _, _ = _make_tier_closer(max_age=120)
        assert closer._get_tier('BTC', 59) == _TIER_NORMAL
        assert closer._get_tier('BTC', 60) == _TIER_BREAKEVEN
        assert closer._get_tier('BTC', 89) == _TIER_BREAKEVEN
        assert closer._get_tier('BTC', 90) == _TIER_AGGRESSIVE

    def test_min_seconds_floor(self):
        from strategies.mm_config import CloseTierToxicityConfig  # noqa: F401
        closer, _, _ = _make_tier_closer(
            max_age=120, coin_tiers={'BTC': (0.1, 0.2)}, min_seconds=30.0)
        # Raw thresholds 12s/24s are floored to 30s/30s
        assert closer._get_tier('BTC', 29) == _TIER_NORMAL
        assert closer._get_tier('BTC', 30) == _TIER_AGGRESSIVE

    def test_min_seconds_zero_disabled(self):
        closer, _, _ = _make_tier_closer(
            max_age=120, coin_tiers={'BTC': (0.1, 0.2)}, min_seconds=0.0)
        assert closer._get_tier('BTC', 12) == _TIER_BREAKEVEN
        assert closer._get_tier('BTC', 24) == _TIER_AGGRESSIVE

    def test_toxicity_and_override_stack_with_max_age_override(self):
        from strategies.mm_config import CloseTierToxicityConfig
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -3.0}})
        cfg = CloseTierToxicityConfig(enabled=True, threshold_bps=-2.0,
                                      multiplier=0.6, floor_pct=0.15)
        closer, _, _ = _make_tier_closer(
            max_age=120, coin_tiers={'BTC': (0.30, 0.55)},
            tox_cfg=cfg, tracker=tracker)
        # dynamic-age style override: max_age 90s
        # breakeven = 90 * 0.30 * 0.6 = 16.2s, aggressive = 90 * 0.55 * 0.6 = 29.7s
        assert closer._get_tier('BTC', 16, max_age=90) == _TIER_NORMAL
        assert closer._get_tier('BTC', 17, max_age=90) == _TIER_BREAKEVEN
        assert closer._get_tier('BTC', 30, max_age=90) == _TIER_AGGRESSIVE

    def test_min_seconds_bounds_toxicity_stack(self):
        from strategies.mm_config import CloseTierToxicityConfig
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -3.0}})
        cfg = CloseTierToxicityConfig(enabled=True, threshold_bps=-2.0,
                                      multiplier=0.6, floor_pct=0.15)
        closer, _, _ = _make_tier_closer(
            max_age=120, coin_tiers={'BTC': (0.30, 0.55)},
            tox_cfg=cfg, tracker=tracker, min_seconds=20.0)
        # Floored: breakeven max(16.2, 20) = 20s
        assert closer._get_tier('BTC', 17, max_age=90) == _TIER_NORMAL
        assert closer._get_tier('BTC', 20, max_age=90) == _TIER_BREAKEVEN


class TestManageWithTierOverrides:
    """manage() integration: earlier tightening, unchanged force close."""

    def test_tightens_earlier_with_override(self):
        closer, om, md = _make_tier_closer(
            max_age=120, coin_tiers={'BTC': (0.25, 0.50)})

        # Aged 35s: global config would still be TIER_NORMAL (< 60s),
        # but the 0.25 override puts breakeven at 30s.
        entry_time = time.monotonic() - 35
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_NORMAL)
        om.get_open_orders.return_value = [{'oid': 42}]

        mock_order = MagicMock()
        mock_order.id = 99
        om.create_limit_order.return_value = mock_order

        closer.manage('BTC', {'size': 0.5, 'entry_price': 50000.0}, MagicMock())

        om.cancel_order.assert_called_once_with(42, 'BTC')
        assert closer._open_positions['BTC'][2] == _TIER_BREAKEVEN

    def test_toxicity_tightens_earlier_via_manage(self):
        from strategies.mm_config import CloseTierToxicityConfig
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': 0.0}})
        cfg = CloseTierToxicityConfig(enabled=True, threshold_bps=-2.0,
                                      multiplier=0.6)
        closer, om, md = _make_tier_closer(max_age=120, tox_cfg=cfg, tracker=tracker)

        # Aged 40s with a live close order: base breakeven is at 60s,
        # accelerated breakeven at 120 * 0.50 * 0.6 = 36s.
        entry_time = time.monotonic() - 40
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_NORMAL)
        om.get_open_orders.return_value = [{'oid': 42}]
        mock_order = MagicMock()
        mock_order.id = 99
        om.create_limit_order.return_value = mock_order

        # Benign markout: no tightening yet at 40s
        closer.manage('BTC', {'size': 0.5, 'entry_price': 50000.0}, MagicMock())
        om.cancel_order.assert_not_called()

        # Markout degrades -> acceleration fires -> tightening at the same age
        tracker._stats['BTC']['avg_30s'] = -5.0
        closer.manage('BTC', {'size': 0.5, 'entry_price': 50000.0}, MagicMock())
        om.cancel_order.assert_called_once_with(42, 'BTC')
        assert closer._open_positions['BTC'][2] == _TIER_BREAKEVEN

        # Markout recovers: desired tier drops back to NORMAL but the
        # placed order must NOT loosen (tier is monotonic per position)
        om.get_open_orders.return_value = [{'oid': 99}]
        om.cancel_order.reset_mock()
        tracker._stats['BTC']['avg_30s'] = 0.5
        closer.manage('BTC', {'size': 0.5, 'entry_price': 50000.0}, MagicMock())
        om.cancel_order.assert_not_called()
        assert closer._open_positions['BTC'][2] == _TIER_BREAKEVEN

    def test_force_close_age_unchanged_by_toxicity(self):
        from strategies.mm_config import CloseTierToxicityConfig
        tracker = _StubTracker(stats={'BTC': {'fills': 10, 'avg_30s': -5.0}})
        cfg = CloseTierToxicityConfig(enabled=True, threshold_bps=-2.0)
        closer, om, md = _make_tier_closer(
            max_age=120, tox_cfg=cfg, tracker=tracker, maker_only=False)

        entry_time = time.monotonic() - 119
        closer._open_positions['BTC'] = (entry_time, None, _TIER_AGGRESSIVE)
        om.get_open_orders.return_value = []
        mock_order = MagicMock()
        mock_order.id = 99
        om.create_limit_order.return_value = mock_order

        close_fn = MagicMock()
        closer.manage('BTC', {'size': 0.5, 'entry_price': 50000.0}, close_fn)
        # 119s < 120s: not force-closed even with toxicity firing
        close_fn.assert_not_called()

        closer._open_positions['BTC'] = (time.monotonic() - 121, None, _TIER_AGGRESSIVE)
        closer.manage('BTC', {'size': 0.5, 'entry_price': 50000.0}, close_fn)
        # 121s >= 120s: force close fires exactly as before
        close_fn.assert_called_once_with('BTC')
