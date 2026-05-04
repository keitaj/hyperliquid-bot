"""Integration tests for the Forager auto-exclude path.

The strategy initialises a ``CoinHealthTracker`` when
``forager_enabled=True`` and consults it via ``_check_forager_health``
in the per-coin run loop. Tests here exercise the trigger / no-trigger
paths and the cooldown lifecycle.
"""

import time
from unittest.mock import MagicMock

from strategies import market_making_strategy as mm_mod
from strategies.coin_health_tracker import CoinHealth, CoinHealthTracker
from strategies.market_making_strategy import MarketMakingStrategy
from strategies.mm_config import ForagerConfig


def _make_strategy(**extra):
    """Build a MarketMakingStrategy with Forager defaults applied."""
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
        # Forager defaults; tests override score by stubbing tracker.get_health.
        'forager_enabled': True,
        'forager_score_threshold': 30.0,
        'forager_consecutive': 3,
        'forager_cooldown_seconds': 1800,
        'forager_weight_activity': 0.3,
        'forager_weight_quality': 0.4,
        'forager_weight_cost': 0.3,
        'forager_window_seconds': 1800.0,
        'forager_check_interval_seconds': 0.0,  # disable throttle for tests
        'forager_activity_idle_min_seconds': 300.0,
        'forager_cost_max_per_1k': 0.6,
        'forager_min_closes_for_quality': 5,
        **extra,
    }
    strategy = MarketMakingStrategy(market_data, order_manager, config)
    return strategy


def _stub_health(score: float, n_closes: int = 10, activity: float = 50.0) -> CoinHealth:
    return CoinHealth(
        activity_score=activity,
        close_quality_score=10.0,
        cost_score=10.0,
        composite_score=score,
        n_closes=n_closes,
        last_fill_age=120.0,
    )


# --------------------------------------------------------------------------- #
# Disabled path
# --------------------------------------------------------------------------- #


def test_disabled_no_op():
    s = _make_strategy(forager_enabled=False)
    # When disabled, the tracker is None and the run-loop helper short-circuits.
    assert s._coin_health_tracker is None
    s._check_forager_health("BTC")
    assert "BTC" not in s._coin_cooldown_until


# --------------------------------------------------------------------------- #
# Trigger / no-trigger paths
# --------------------------------------------------------------------------- #


def test_does_not_trigger_below_consecutive_count(monkeypatch):
    s = _make_strategy(forager_consecutive=3)
    s._coin_health_tracker = MagicMock()
    s._coin_health_tracker.get_health.return_value = _stub_health(score=10.0)
    # First two checks build the history but should not trigger.
    s._check_forager_health("BTC")
    s._check_forager_health("BTC")
    assert "BTC" not in s._coin_cooldown_until


def test_triggers_after_consecutive_low_scores(monkeypatch):
    s = _make_strategy(forager_consecutive=3)
    s._coin_health_tracker = MagicMock()
    s._coin_health_tracker.get_health.return_value = _stub_health(score=10.0)
    # Three consecutive low scores → cooldown set.
    s._check_forager_health("BTC")
    s._check_forager_health("BTC")
    s._check_forager_health("BTC")
    assert "BTC" in s._coin_cooldown_until
    assert s._coin_cooldown_until["BTC"] > time.monotonic()


def test_no_trigger_when_score_above_threshold():
    s = _make_strategy(forager_consecutive=3)
    s._coin_health_tracker = MagicMock()
    s._coin_health_tracker.get_health.return_value = _stub_health(score=80.0)
    for _ in range(5):
        s._check_forager_health("BTC")
    assert "BTC" not in s._coin_cooldown_until


def test_intermittent_low_score_does_not_trigger():
    """Low scores must be CONSECUTIVE; one good score in between resets."""
    s = _make_strategy(forager_consecutive=3)
    tracker = MagicMock()
    s._coin_health_tracker = tracker

    # Pattern: low, low, high, low, low → no trigger because the high
    # entry pushes the third "low" out of the consecutive window.
    scores = [10.0, 10.0, 80.0, 10.0, 10.0]
    for sc in scores:
        tracker.get_health.return_value = _stub_health(score=sc)
        s._check_forager_health("BTC")
    assert "BTC" not in s._coin_cooldown_until


def test_skips_when_quality_data_insufficient_and_active():
    """Active coin with too few closes should not trigger (false-positive guard)."""
    s = _make_strategy(forager_consecutive=3, forager_min_closes_for_quality=10)
    tracker = MagicMock()
    s._coin_health_tracker = tracker
    # Activity high (>50), n_closes < min_closes_for_quality, score below threshold.
    tracker.get_health.return_value = _stub_health(score=10.0, n_closes=2, activity=100.0)
    for _ in range(5):
        s._check_forager_health("BTC")
    assert "BTC" not in s._coin_cooldown_until


def test_triggers_when_inactive_even_without_close_data():
    """Activity ≤ 50 means the coin is dead — quality gate doesn't protect it."""
    s = _make_strategy(forager_consecutive=3, forager_min_closes_for_quality=10)
    tracker = MagicMock()
    s._coin_health_tracker = tracker
    # No fills (activity 0) and no closes — exactly the flx:COPPER case.
    tracker.get_health.return_value = _stub_health(score=10.0, n_closes=0, activity=0.0)
    s._check_forager_health("BTC")
    s._check_forager_health("BTC")
    s._check_forager_health("BTC")
    assert "BTC" in s._coin_cooldown_until


# --------------------------------------------------------------------------- #
# Cooldown lifecycle
# --------------------------------------------------------------------------- #


def test_skips_evaluation_during_cooldown():
    s = _make_strategy()
    # Manually set cooldown to ~5 minutes from now.
    s._coin_cooldown_until["BTC"] = time.monotonic() + 300.0
    s._coin_health_tracker = MagicMock()
    s._coin_health_tracker.get_health.return_value = _stub_health(score=10.0)
    s._check_forager_health("BTC")
    # Tracker should not be queried — early return on cooldown.
    s._coin_health_tracker.get_health.assert_not_called()


def test_throttle_blocks_repeat_check_within_interval(monkeypatch):
    s = _make_strategy(forager_check_interval_seconds=60.0, forager_consecutive=3)
    tracker = MagicMock()
    s._coin_health_tracker = tracker
    tracker.get_health.return_value = _stub_health(score=10.0)

    # Control time so the second call is inside the throttle window.
    state = {"t": 1000.0}
    monkeypatch.setattr(mm_mod.time, "monotonic", lambda: state["t"])

    s._check_forager_health("BTC")  # records last_check
    state["t"] += 30.0  # within 60s interval
    s._check_forager_health("BTC")  # should be throttled
    state["t"] += 30.0
    s._check_forager_health("BTC")
    # Only the first invocation got through; the second was throttled and
    # the third is post-window — so we have at most 2 evaluations, not 3.
    assert tracker.get_health.call_count < 3


# --------------------------------------------------------------------------- #
# Co-existence with auto_exclude
# --------------------------------------------------------------------------- #


def test_does_not_re_trigger_if_auto_exclude_set_cooldown():
    """If auto_exclude already set the cooldown, Forager early-returns."""
    s = _make_strategy()
    # Simulate auto_exclude setting cooldown.
    s._coin_cooldown_until["BTC"] = time.monotonic() + 1800.0
    tracker = MagicMock()
    s._coin_health_tracker = tracker
    tracker.get_health.return_value = _stub_health(score=10.0)
    s._check_forager_health("BTC")
    s._check_forager_health("BTC")
    s._check_forager_health("BTC")
    # The cooldown was already set by the (mocked) auto_exclude — Forager
    # should not modify it.
    assert tracker.get_health.call_count == 0


# --------------------------------------------------------------------------- #
# Defensive: bypass-init test fixtures should not crash
# --------------------------------------------------------------------------- #


def test_check_no_op_when_strategy_constructed_with_new(monkeypatch):
    """Tests that bypass __init__ via __new__ should still be safe.

    Mirrors the defensive ``getattr(self, 'cfg', None)`` pattern used by
    auto_exclude: those test fixtures don't set ``self.cfg`` at all.
    """
    s = MarketMakingStrategy.__new__(MarketMakingStrategy)
    # No attributes set; the helper must short-circuit cleanly.
    s._check_forager_health("BTC")  # must not raise


# --------------------------------------------------------------------------- #
# Config-driven tunability (anti-hardcode regression)
# --------------------------------------------------------------------------- #


def test_threshold_reads_from_config_not_hardcoded(monkeypatch):
    """If we change ``forager_score_threshold`` to 50, score=40 should
    trigger even though it would not at default 30."""
    s = _make_strategy(forager_consecutive=3, forager_score_threshold=50.0)
    tracker = MagicMock()
    s._coin_health_tracker = tracker
    tracker.get_health.return_value = _stub_health(score=40.0)
    s._check_forager_health("BTC")
    s._check_forager_health("BTC")
    s._check_forager_health("BTC")
    assert "BTC" in s._coin_cooldown_until


def test_cooldown_seconds_reads_from_config():
    s = _make_strategy(forager_cooldown_seconds=42)
    tracker = MagicMock()
    s._coin_health_tracker = tracker
    tracker.get_health.return_value = _stub_health(score=10.0)
    before = time.monotonic()
    for _ in range(3):
        s._check_forager_health("BTC")
    deadline = s._coin_cooldown_until["BTC"]
    # Cooldown should be roughly 42s in the future, not 1800.
    assert 30 < (deadline - before) < 60


def test_tracker_uses_supplied_forager_config():
    """``CoinHealthTracker`` must use the strategy's ForagerConfig, not
    its own hardcoded defaults."""
    custom_cfg = ForagerConfig(enabled=True, cost_max_per_1k=0.1)
    tracker = CoinHealthTracker(custom_cfg)
    assert tracker.config is custom_cfg
    assert tracker.config.cost_max_per_1k == 0.1
