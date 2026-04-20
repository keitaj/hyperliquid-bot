"""Tests for dynamic offset auto-adjustment based on adverse selection."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(dynamic_offset_enabled=False, **extra):
    """Create a MarketMakingStrategy with mocked dependencies."""
    market_data = MagicMock()
    order_manager = MagicMock()
    config = {
        'spread_bps': 10,
        'order_size_usd': 200,
        'max_open_orders': 4,
        'close_immediately': False,
        'max_positions': 8,
        'maker_only': True,
        'bbo_mode': True,
        'bbo_offset_bps': 1.0,
        'dynamic_offset_enabled': dynamic_offset_enabled,
        'dynamic_offset_sensitivity': 0.5,
        'dynamic_offset_tighten_rate': 0.25,
        'dynamic_offset_max_addition': 3.0,
        'dynamic_offset_max_reduction': 1.0,
        'dynamic_offset_floor': 0.5,
        'dynamic_offset_min_fills': 5,
        **extra,
    }
    strategy = MarketMakingStrategy(market_data, order_manager, config)
    return strategy


def _mock_tracker(stats: dict) -> MagicMock:
    """Create a mock AdverseSelectionTracker with given stats."""
    tracker = MagicMock()
    type(tracker).stats = PropertyMock(return_value=stats)
    return tracker


# ── Disabled / no tracker ────────────────────────────────────────────

class TestDynamicOffsetDisabled:
    """When feature is disabled, base offset is returned unchanged."""

    def test_disabled_returns_base(self):
        strategy = _make_strategy(dynamic_offset_enabled=False)
        strategy._adverse_tracker = _mock_tracker({"xyz:SP500": {"fills": 20, "avg_5s": -3.0}})
        assert strategy._get_coin_offset("xyz:SP500") == 1.0  # global default

    def test_no_tracker_returns_base(self):
        strategy = _make_strategy(dynamic_offset_enabled=True)
        # _adverse_tracker is None by default
        assert strategy._get_coin_offset("xyz:SP500") == 1.0

    def test_coin_override_returned_when_disabled(self):
        strategy = _make_strategy(
            dynamic_offset_enabled=False,
            coin_offset_overrides="SP500:2.0",
        )
        assert strategy._get_coin_offset("xyz:SP500") == 2.0


# ── Insufficient data ───────────────────────────────────────────────

class TestDynamicOffsetInsufficientData:
    """When fills < min_fills, base offset is returned."""

    def test_no_coin_in_stats(self):
        strategy = _make_strategy(dynamic_offset_enabled=True)
        strategy._adverse_tracker = _mock_tracker({})
        assert strategy._get_coin_offset("xyz:SP500") == 1.0

    def test_fills_below_minimum(self):
        strategy = _make_strategy(dynamic_offset_enabled=True, dynamic_offset_min_fills=5)
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 3, "avg_5s": -5.0}
        })
        assert strategy._get_coin_offset("xyz:SP500") == 1.0  # not enough fills


# ── Adverse selection widens offset ─────────────────────────────────

class TestDynamicOffsetWidening:
    """When adverse selection is negative, offset widens."""

    def test_moderate_adverse_selection(self):
        strategy = _make_strategy(dynamic_offset_enabled=True)
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 10, "avg_5s": -2.0}
        })
        # base=1.0, adjustment = 2.0 * 0.5 = 1.0 → result = 2.0
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(2.0)

    def test_severe_adverse_selection(self):
        strategy = _make_strategy(dynamic_offset_enabled=True)
        strategy._adverse_tracker = _mock_tracker({
            "xyz:META": {"fills": 15, "avg_5s": -4.0}
        })
        # base=1.0, adjustment = 4.0 * 0.5 = 2.0 → result = 3.0
        assert strategy._get_coin_offset("xyz:META") == pytest.approx(3.0)

    def test_with_manual_override_base(self):
        strategy = _make_strategy(
            dynamic_offset_enabled=True,
            coin_offset_overrides="SP500:2.0",
        )
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 10, "avg_5s": -2.0}
        })
        # base=2.0, adjustment = 2.0 * 0.5 = 1.0 → result = 3.0
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(3.0)

    def test_max_addition_clamp(self):
        strategy = _make_strategy(dynamic_offset_enabled=True, dynamic_offset_max_addition=3.0)
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 20, "avg_5s": -10.0}
        })
        # base=1.0, raw adjustment = 10.0 * 0.5 = 5.0, clamped to 3.0 → result = 4.0
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(4.0)


# ── Favorable selection tightens offset ──────────────────────────────

class TestDynamicOffsetTightening:
    """When fills are favorable, offset tightens."""

    def test_favorable_tightens(self):
        strategy = _make_strategy(dynamic_offset_enabled=True)
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 10, "avg_5s": 2.0}
        })
        # base=1.0, adjustment = -(2.0 * 0.25) = -0.5 → result = 0.5
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(0.5)

    def test_floor_prevents_too_low(self):
        strategy = _make_strategy(dynamic_offset_enabled=True, dynamic_offset_floor=0.5)
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 10, "avg_5s": 8.0}
        })
        # base=1.0, adjustment = -(8.0 * 0.25) = -2.0, clamped to -1.0 → result=0.0 → floor=0.5
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(0.5)

    def test_max_reduction_clamp(self):
        strategy = _make_strategy(
            dynamic_offset_enabled=True,
            dynamic_offset_max_reduction=1.0,
            dynamic_offset_floor=0.0,
            coin_offset_overrides="SP500:3.0",
        )
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 10, "avg_5s": 10.0}
        })
        # base=3.0, raw adjustment = -(10.0 * 0.25) = -2.5, clamped to -1.0 → result = 2.0
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(2.0)


# ── Zero adverse selection ──────────────────────────────────────────

class TestDynamicOffsetZero:
    """When adverse selection is exactly zero, no adjustment."""

    def test_zero_adverse_no_change(self):
        strategy = _make_strategy(dynamic_offset_enabled=True)
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 10, "avg_5s": 0.0}
        })
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(1.0)

    def test_missing_avg_5s_key(self):
        strategy = _make_strategy(dynamic_offset_enabled=True)
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 10}  # no avg_5s key
        })
        # defaults to 0.0 → no adjustment
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(1.0)


# ── Custom parameters ───────────────────────────────────────────────

class TestDynamicOffsetCustomParams:
    """Tests with non-default parameter values."""

    def test_high_sensitivity(self):
        strategy = _make_strategy(
            dynamic_offset_enabled=True,
            dynamic_offset_sensitivity=1.0,
        )
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 10, "avg_5s": -2.0}
        })
        # base=1.0, adjustment = 2.0 * 1.0 = 2.0 → result = 3.0
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(3.0)

    def test_custom_min_fills(self):
        strategy = _make_strategy(
            dynamic_offset_enabled=True,
            dynamic_offset_min_fills=10,
        )
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 9, "avg_5s": -5.0}
        })
        # fills=9 < min_fills=10 → base offset
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(1.0)

    def test_custom_floor(self):
        strategy = _make_strategy(
            dynamic_offset_enabled=True,
            dynamic_offset_floor=1.5,
        )
        strategy._adverse_tracker = _mock_tracker({
            "xyz:SP500": {"fills": 10, "avg_5s": 4.0}
        })
        # base=1.0, adjustment = -(4.0 * 0.25) = -1.0, clamped to -1.0 → 0.0 → floor=1.5
        assert strategy._get_coin_offset("xyz:SP500") == pytest.approx(1.5)
