"""Tests for auto-exclude on consecutive adverse-selection windows.

The strategy reads ``MMConfig.auto_exclude`` and consults
``AdverseSelectionTracker.get_recent_windows`` from the existing
``run()`` per-coin loop. This module tests the trigger / no-trigger
paths and the cooldown lifecycle in isolation by calling
``_check_auto_exclude`` directly.
"""

import time
from unittest.mock import MagicMock

import pytest

from strategies.market_making_strategy import MarketMakingStrategy


def _make_strategy(**extra):
    """Build a MarketMakingStrategy with sane defaults and an MM config dict."""
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
        # auto_exclude defaults: enabled, threshold=-3.0, consecutive=3,
        # min_fills=5, cooldown=1800, window=60s
        'auto_exclude_enabled': True,
        'auto_exclude_threshold_bps': -3.0,
        'auto_exclude_consecutive': 3,
        'auto_exclude_min_fills': 5,
        'auto_exclude_cooldown': 1800,
        'auto_exclude_window_label': '60s',
        **extra,
    }
    strategy = MarketMakingStrategy(market_data, order_manager, config)
    return strategy


def _tracker_returning(history):
    """Mock AdverseSelectionTracker whose get_recent_windows returns ``history``."""
    tracker = MagicMock()
    tracker.get_recent_windows.return_value = history
    return tracker


def _window(avg_60s, fills=10, avg_5s=None, avg_30s=None):
    return {
        'ts': time.time(),
        'fills': fills,
        'avg_5s': avg_5s if avg_5s is not None else avg_60s,
        'avg_30s': avg_30s if avg_30s is not None else avg_60s,
        'avg_60s': avg_60s,
    }


# ── Disabled / no tracker ─────────────────────────────────────────

class TestDisabled:
    def test_disabled_does_nothing(self):
        strategy = _make_strategy(auto_exclude_enabled=False)
        strategy._adverse_tracker = _tracker_returning(
            [_window(-5.0), _window(-5.0), _window(-5.0)]
        )
        strategy._check_auto_exclude("xyz:MSFT")
        assert "xyz:MSFT" not in strategy._coin_cooldown_until

    def test_no_tracker_does_nothing(self):
        strategy = _make_strategy()
        # _adverse_tracker is None by default
        strategy._check_auto_exclude("xyz:MSFT")
        assert "xyz:MSFT" not in strategy._coin_cooldown_until


# ── Trigger conditions ────────────────────────────────────────────

class TestTrigger:
    def test_triggers_on_n_consecutive_below_threshold(self):
        strategy = _make_strategy()
        strategy._adverse_tracker = _tracker_returning(
            [_window(-3.5), _window(-4.0), _window(-3.2)]
        )
        before = time.monotonic()
        strategy._check_auto_exclude("xyz:MSFT")
        deadline = strategy._coin_cooldown_until.get("xyz:MSFT")
        assert deadline is not None
        # Cooldown deadline should be ~1800s in the future
        assert deadline >= before + 1799
        assert deadline <= before + 1801

    def test_does_not_trigger_on_borderline_window(self):
        # One window at exactly the threshold (= -3.0) does not trigger
        # because the comparison is "> threshold", but at -2.9 (above
        # threshold = better than threshold) the window is considered fine.
        strategy = _make_strategy()
        strategy._adverse_tracker = _tracker_returning(
            [_window(-3.5), _window(-2.9), _window(-3.5)]
        )
        strategy._check_auto_exclude("xyz:MSFT")
        assert "xyz:MSFT" not in strategy._coin_cooldown_until

    def test_threshold_exact_match_triggers(self):
        # avg == threshold (-3.0) should trigger (<=)
        strategy = _make_strategy()
        strategy._adverse_tracker = _tracker_returning(
            [_window(-3.0), _window(-3.0), _window(-3.0)]
        )
        strategy._check_auto_exclude("xyz:MSFT")
        assert "xyz:MSFT" in strategy._coin_cooldown_until

    def test_skipped_when_history_short(self):
        strategy = _make_strategy()
        strategy._adverse_tracker = _tracker_returning(
            [_window(-3.5), _window(-4.0)]  # only 2 windows, need 3
        )
        strategy._check_auto_exclude("xyz:MSFT")
        assert "xyz:MSFT" not in strategy._coin_cooldown_until

    def test_min_fills_blocks_trigger(self):
        strategy = _make_strategy(auto_exclude_min_fills=5)
        strategy._adverse_tracker = _tracker_returning([
            _window(-3.5, fills=5),
            _window(-3.5, fills=2),  # below min_fills
            _window(-3.5, fills=5),
        ])
        strategy._check_auto_exclude("xyz:MSFT")
        assert "xyz:MSFT" not in strategy._coin_cooldown_until

    def test_none_avg_blocks_trigger(self):
        # When avg is None (no fills produced a sample for the window),
        # treat it as insufficient evidence and do not trigger.
        strategy = _make_strategy()
        strategy._adverse_tracker = _tracker_returning([
            _window(-3.5),
            {'ts': 0, 'fills': 10, 'avg_5s': None, 'avg_30s': None, 'avg_60s': None},
            _window(-3.5),
        ])
        strategy._check_auto_exclude("xyz:MSFT")
        assert "xyz:MSFT" not in strategy._coin_cooldown_until

    def test_window_label_30s_used_when_configured(self):
        strategy = _make_strategy(auto_exclude_window_label='30s')
        # 60s avg looks fine, 30s avg is bad — the 30s label drives the call
        bad = lambda: {                                                       # noqa: E731
            'ts': 0, 'fills': 10,
            'avg_5s': -1.0,
            'avg_30s': -4.0,
            'avg_60s': -1.0,
        }
        strategy._adverse_tracker = _tracker_returning([bad(), bad(), bad()])
        strategy._check_auto_exclude("xyz:MSFT")
        assert "xyz:MSFT" in strategy._coin_cooldown_until


# ── Cooldown lifecycle ────────────────────────────────────────────

class TestCooldownLifecycle:
    def test_existing_cooldown_is_not_overwritten(self):
        strategy = _make_strategy()
        # Pre-set a cooldown that is still in the future
        far_future = time.monotonic() + 9999
        strategy._coin_cooldown_until["xyz:MSFT"] = far_future
        strategy._adverse_tracker = _tracker_returning(
            [_window(-5.0), _window(-5.0), _window(-5.0)]
        )
        strategy._check_auto_exclude("xyz:MSFT")
        # Deadline should remain the original far-future value
        assert strategy._coin_cooldown_until["xyz:MSFT"] == far_future

    def test_expired_cooldown_does_not_block_new_trigger(self):
        strategy = _make_strategy()
        # Pre-set a cooldown that is already in the past
        strategy._coin_cooldown_until["xyz:MSFT"] = time.monotonic() - 1
        strategy._adverse_tracker = _tracker_returning(
            [_window(-3.5), _window(-3.5), _window(-3.5)]
        )
        before = time.monotonic()
        strategy._check_auto_exclude("xyz:MSFT")
        new_deadline = strategy._coin_cooldown_until["xyz:MSFT"]
        assert new_deadline >= before + 1799


# ── Integration with loss_streak shared cooldown map ──────────────

class TestSharedCooldownMap:
    def test_loss_streak_deadline_blocks_auto_exclude_check(self):
        # When loss_streak has already set a deadline, auto_exclude leaves it alone.
        strategy = _make_strategy(loss_streak_limit=3, loss_streak_cooldown=600)
        loss_streak_deadline = time.monotonic() + 600
        strategy._coin_cooldown_until["xyz:MSFT"] = loss_streak_deadline
        strategy._adverse_tracker = _tracker_returning(
            [_window(-5.0), _window(-5.0), _window(-5.0)]
        )
        strategy._check_auto_exclude("xyz:MSFT")
        # loss_streak's deadline is preserved (auto_exclude didn't overwrite)
        assert strategy._coin_cooldown_until["xyz:MSFT"] == loss_streak_deadline


# ── Validation propagation through MMConfig ───────────────────────

class TestConstructorValidation:
    def test_invalid_window_label_rejected_at_construction(self):
        with pytest.raises(ValueError, match='auto_exclude_window_label'):
            _make_strategy(auto_exclude_window_label='90s')

    def test_invalid_consecutive_rejected_at_construction(self):
        with pytest.raises(ValueError, match='auto_exclude_consecutive'):
            _make_strategy(auto_exclude_consecutive=0)
