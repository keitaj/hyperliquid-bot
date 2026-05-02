"""Tests for volatility-adjusted MAX_POSITION_AGE (dynamic age).

Covers:
1. Disabled by default -- manage() receives None, old behavior preserved
2. High volatility -- position age shortened
3. Low volatility -- position age extended
4. Min/max clamping
5. Insufficient data -- returns None
6. PositionCloser.manage() max_age_override behavior
7. _get_tier() respects effective_max_age
"""

import time
from collections import deque
from unittest.mock import MagicMock, patch

from strategies.mm_position_closer import (
    PositionCloser,
    _TIER_AGGRESSIVE,
    _TIER_BREAKEVEN,
    _TIER_NORMAL,
)
from strategies.market_making_strategy import MarketMakingStrategy


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #


def _make_closer(max_age: float = 120, maker_only: bool = False,
                 taker_fallback: float = None, spread_bps: float = 10) -> tuple:
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


def _make_strategy(dynamic_age_enabled: bool = False,
                   dynamic_age_baseline_vol: float = 1.0,
                   dynamic_age_min: float = 60.0,
                   dynamic_age_max: float = 300.0,
                   max_position_age_seconds: float = 120.0,
                   **extra) -> MarketMakingStrategy:
    config = {
        'spread_bps': 5,
        'order_size_usd': 50,
        'max_open_orders': 4,
        'close_immediately': False,
        'max_positions': 3,
        'maker_only': False,
        'max_position_age_seconds': max_position_age_seconds,
        'dynamic_age_enabled': dynamic_age_enabled,
        'dynamic_age_baseline_vol': dynamic_age_baseline_vol,
        'dynamic_age_min': dynamic_age_min,
        'dynamic_age_max': dynamic_age_max,
    }
    config.update(extra)
    with patch.object(MarketMakingStrategy, '__init__', lambda self, *a, **kw: None):
        strategy = MarketMakingStrategy.__new__(MarketMakingStrategy)

    # Manually set only the fields needed for _get_dynamic_position_age
    strategy._dynamic_age_enabled = config['dynamic_age_enabled']
    strategy._dynamic_age_baseline_vol = config['dynamic_age_baseline_vol']
    strategy._dynamic_age_min = config['dynamic_age_min']
    strategy._dynamic_age_max = config['dynamic_age_max']
    strategy._base_max_position_age = config['max_position_age_seconds']
    strategy.vol_adjust_enabled = config.get('vol_adjust_enabled', False)
    strategy.vol_lookback = config.get('vol_lookback', 30)
    strategy._recent_mids = {}
    strategy._dynamic_age_recent = {}
    strategy._dynamic_age_clamp_stats = {}
    strategy._dynamic_age_log_interval = 300.0
    strategy._last_dynamic_age_log = 0.0
    return strategy


# ------------------------------------------------------------------ #
#  Strategy: _get_dynamic_position_age
# ------------------------------------------------------------------ #


class TestDynamicPositionAgeDisabled:
    """When dynamic_age_enabled=False, _get_dynamic_position_age returns None."""

    def test_disabled_returns_none(self):
        strategy = _make_strategy(dynamic_age_enabled=False)
        assert strategy._get_dynamic_position_age('BTC') is None

    def test_disabled_even_with_mids(self):
        strategy = _make_strategy(dynamic_age_enabled=False)
        strategy._recent_mids['BTC'] = deque([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        assert strategy._get_dynamic_position_age('BTC') is None


class TestDynamicPositionAgeInsufficientData:
    """With fewer than 5 mid prices, returns None."""

    def test_no_mids(self):
        strategy = _make_strategy(dynamic_age_enabled=True)
        assert strategy._get_dynamic_position_age('BTC') is None

    def test_too_few_mids(self):
        strategy = _make_strategy(dynamic_age_enabled=True)
        strategy._recent_mids['BTC'] = deque([100.0, 101.0, 102.0, 103.0])
        assert strategy._get_dynamic_position_age('BTC') is None


class TestDynamicPositionAgeHighVol:
    """High volatility -> shorter position age."""

    def test_double_baseline_vol_halves_age(self):
        strategy = _make_strategy(
            dynamic_age_enabled=True,
            dynamic_age_baseline_vol=1.0,
            max_position_age_seconds=120.0,
            dynamic_age_min=60.0,
            dynamic_age_max=300.0,
        )
        # avg_move_bps = 2.0 -> ratio = 1.0/2.0 = 0.5 -> age = 120*0.5 = 60
        # Create mids where each step moves ~2 bps (0.02%)
        base = 10000.0
        mids = deque()
        for i in range(10):
            mids.append(base + i * 2.0)  # ~2 bps per step at base 10000
        strategy._recent_mids['BTC'] = mids

        age = strategy._get_dynamic_position_age('BTC')
        assert age is not None
        assert abs(age - 60.0) < 1.0  # clamped to min


class TestDynamicPositionAgeLowVol:
    """Low volatility -> longer position age."""

    def test_half_baseline_vol_doubles_age(self):
        strategy = _make_strategy(
            dynamic_age_enabled=True,
            dynamic_age_baseline_vol=1.0,
            max_position_age_seconds=120.0,
            dynamic_age_min=60.0,
            dynamic_age_max=300.0,
        )
        # avg_move_bps = 0.5 -> ratio = 1.0/0.5 = 2.0 -> age = 120*2 = 240
        base = 10000.0
        mids = deque()
        for i in range(10):
            mids.append(base + i * 0.5)  # ~0.5 bps per step at base 10000
        strategy._recent_mids['BTC'] = mids

        age = strategy._get_dynamic_position_age('BTC')
        assert age is not None
        assert abs(age - 240.0) < 1.0


class TestDynamicPositionAgeClamp:
    """Extreme volatility is clamped to min/max bounds."""

    def test_extreme_high_vol_clamps_to_min(self):
        strategy = _make_strategy(
            dynamic_age_enabled=True,
            dynamic_age_baseline_vol=1.0,
            max_position_age_seconds=120.0,
            dynamic_age_min=60.0,
            dynamic_age_max=300.0,
        )
        # Very high vol: each step ~50 bps
        base = 10000.0
        mids = deque()
        for i in range(10):
            mids.append(base + i * 50.0)
        strategy._recent_mids['BTC'] = mids

        age = strategy._get_dynamic_position_age('BTC')
        assert age == 60.0

    def test_extreme_low_vol_clamps_to_max(self):
        strategy = _make_strategy(
            dynamic_age_enabled=True,
            dynamic_age_baseline_vol=1.0,
            max_position_age_seconds=120.0,
            dynamic_age_min=60.0,
            dynamic_age_max=300.0,
        )
        # Very low vol: each step ~0.01 bps (nearly flat)
        base = 10000.0
        mids = deque()
        for i in range(10):
            mids.append(base + i * 0.01)
        strategy._recent_mids['BTC'] = mids

        age = strategy._get_dynamic_position_age('BTC')
        assert age == 300.0

    def test_zero_vol_does_not_divide_by_zero(self):
        """Flat prices (0 bps moves) should not cause division by zero."""
        strategy = _make_strategy(
            dynamic_age_enabled=True,
            dynamic_age_baseline_vol=1.0,
            max_position_age_seconds=120.0,
            dynamic_age_min=60.0,
            dynamic_age_max=300.0,
        )
        # All same price -> avg_move_bps = 0 -> floor = baseline * 0.1
        mids = deque([10000.0] * 10)
        strategy._recent_mids['BTC'] = mids

        age = strategy._get_dynamic_position_age('BTC')
        assert age is not None
        assert age == 300.0  # ratio = 1.0/0.1 = 10 -> age = 1200, clamped to 300


# ------------------------------------------------------------------ #
#  PositionCloser: max_age_override
# ------------------------------------------------------------------ #


class TestPositionCloserMaxAgeOverride:
    """PositionCloser.manage() respects max_age_override."""

    def test_override_triggers_force_close_earlier(self):
        """With override=60, a 65s-old position should be force-closed."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        entry_time = time.monotonic() - 65
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 50000.0}

        closer.manage('BTC', position, close_fn, max_age_override=60.0)

        # 65s > 60s override -> force close
        close_fn.assert_called_once_with('BTC')
        assert 'BTC' not in closer._open_positions

    def test_none_override_uses_default(self):
        """With override=None, default max_age (120s) applies."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        entry_time = time.monotonic() - 65
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 50000.0}

        closer.manage('BTC', position, close_fn, max_age_override=None)

        # 65s < 120s default -> should NOT force close
        close_fn.assert_not_called()

    def test_override_none_explicitly_preserves_default(self):
        """Passing max_age_override=None should use self.max_position_age_seconds=120."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        entry_time = time.monotonic() - 130
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 50000.0}

        closer.manage('BTC', position, close_fn, max_age_override=None)

        # 130s > 120s default -> force close
        close_fn.assert_called_once_with('BTC')


# ------------------------------------------------------------------ #
#  _get_tier with effective_max_age
# ------------------------------------------------------------------ #


class TestGetTierWithMaxAge:
    """_get_tier uses effective_max_age for tier thresholds."""

    def test_short_max_age_aggressive_tier(self):
        """age=45, max_age=60 -> 75% -> aggressive tier."""
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier(45, max_age=60) == _TIER_AGGRESSIVE

    def test_same_age_default_max_normal_tier(self):
        """age=45, default max_age=120 -> 37.5% -> normal tier."""
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier(45) == _TIER_NORMAL

    def test_breakeven_with_custom_max(self):
        """age=45, max_age=80 -> 56% -> breakeven tier."""
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier(45, max_age=80) == _TIER_BREAKEVEN

    def test_none_max_age_uses_default(self):
        """max_age=None -> uses self.max_position_age_seconds."""
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier(45, max_age=None) == _TIER_NORMAL
        assert closer._get_tier(90, max_age=None) == _TIER_AGGRESSIVE


# ------------------------------------------------------------------ #
#  _record_mid_price shared with dynamic age
# ------------------------------------------------------------------ #


class TestRecordMidPriceShared:
    """_record_mid_price records when either vol_adjust or dynamic_age is enabled."""

    def test_records_when_dynamic_age_only(self):
        strategy = _make_strategy(dynamic_age_enabled=True)
        strategy.vol_adjust_enabled = False
        strategy.vol_lookback = 30

        strategy._record_mid_price('BTC', 50000.0)
        assert 'BTC' in strategy._recent_mids
        assert len(strategy._recent_mids['BTC']) == 1

    def test_no_record_when_both_disabled(self):
        strategy = _make_strategy(dynamic_age_enabled=False)
        strategy.vol_adjust_enabled = False

        strategy._record_mid_price('BTC', 50000.0)
        assert 'BTC' not in strategy._recent_mids
