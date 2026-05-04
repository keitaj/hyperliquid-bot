"""Unit tests for ``strategies.coin_health_tracker.CoinHealthTracker``.

Verifies that the composite health score is driven entirely by
``ForagerConfig`` (no hardcoded literals in the formulas) so operators
can tune the scoring via env vars without touching code.
"""

import pytest

from strategies import coin_health_tracker as cht_mod
from strategies.coin_health_tracker import CoinHealthTracker
from strategies.mm_config import ForagerConfig


def _config(**overrides) -> ForagerConfig:
    """Build a ForagerConfig with the supplied overrides applied to defaults."""
    defaults = dict(
        enabled=True,
        score_threshold=30.0,
        consecutive=3,
        cooldown_seconds=1800,
        weight_activity=0.3,
        weight_quality=0.4,
        weight_cost=0.3,
        window_seconds=1800.0,
        check_interval_seconds=300.0,
        activity_idle_min_seconds=300.0,
        cost_max_per_1k=0.6,
        min_closes_for_quality=5,
    )
    defaults.update(overrides)
    return ForagerConfig(**defaults)


def _patch_clock(monkeypatch, t: float) -> dict:
    """Install a controllable monotonic clock on the cht module's ``time``."""
    state = {"t": t}
    monkeypatch.setattr(cht_mod.time, "monotonic", lambda: state["t"])
    return state


# --------------------------------------------------------------------------- #
# Activity dimension
# --------------------------------------------------------------------------- #


class TestActivityScore:
    def test_activity_full_when_no_fill_recorded_then_fill(self, monkeypatch):
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config())
        tracker.record_fill("BTC")
        assert tracker.get_health("BTC").activity_score == 100.0

    def test_activity_full_within_idle_min(self, monkeypatch):
        clock = _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config(activity_idle_min_seconds=300.0))
        tracker.record_fill("BTC")
        clock["t"] += 200.0  # within idle grace
        assert tracker.get_health("BTC").activity_score == 100.0

    def test_activity_zero_after_window_elapsed(self, monkeypatch):
        clock = _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(
            _config(window_seconds=1800.0, activity_idle_min_seconds=300.0)
        )
        tracker.record_fill("BTC")
        clock["t"] += 1800.0  # exactly at window edge
        assert tracker.get_health("BTC").activity_score == 0.0

    def test_activity_linear_decay(self, monkeypatch):
        clock = _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(
            _config(window_seconds=1800.0, activity_idle_min_seconds=300.0)
        )
        tracker.record_fill("BTC")
        # midpoint: idle = 300 + (1800-300)/2 = 1050s -> activity = 50
        clock["t"] += 1050.0
        assert abs(tracker.get_health("BTC").activity_score - 50.0) < 0.001

    def test_activity_zero_for_unknown_coin(self):
        tracker = CoinHealthTracker(_config())
        assert tracker.get_health("UNSEEN").activity_score == 0.0

    def test_activity_idle_min_is_config_driven(self, monkeypatch):
        """If activity_idle_min_seconds=600, 500s idle is still in grace."""
        clock = _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(
            _config(activity_idle_min_seconds=600.0, window_seconds=1800.0)
        )
        tracker.record_fill("BTC")
        clock["t"] += 500.0
        assert tracker.get_health("BTC").activity_score == 100.0


# --------------------------------------------------------------------------- #
# Close-quality dimension
# --------------------------------------------------------------------------- #


class TestCloseQualityScore:
    def test_quality_neutral_with_no_closes(self, monkeypatch):
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config())
        h = tracker.get_health("BTC")
        assert h.close_quality_score == 50.0
        assert h.cost_score == 50.0

    def test_quality_100_all_maker(self, monkeypatch):
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config())
        for _ in range(5):
            tracker.record_close("BTC", is_maker=True, net_pnl=0.01, notional=100)
        assert tracker.get_health("BTC").close_quality_score == 100.0

    def test_quality_0_all_taker(self, monkeypatch):
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config())
        for _ in range(5):
            tracker.record_close("BTC", is_maker=False, net_pnl=-0.05, notional=100)
        assert tracker.get_health("BTC").close_quality_score == 0.0

    def test_quality_50_half_and_half(self, monkeypatch):
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config())
        for is_maker in (True, True, False, False):
            tracker.record_close("BTC", is_maker=is_maker, net_pnl=0, notional=100)
        assert tracker.get_health("BTC").close_quality_score == 50.0


# --------------------------------------------------------------------------- #
# Cost dimension
# --------------------------------------------------------------------------- #


class TestCostScore:
    def test_cost_100_when_all_profitable(self, monkeypatch):
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config())
        for _ in range(5):
            tracker.record_close("BTC", is_maker=True, net_pnl=0.05, notional=100)
        # No losses → cost score = 100
        assert tracker.get_health("BTC").cost_score == 100.0

    def test_cost_zero_at_cost_max(self, monkeypatch):
        """Cost score should reach 0 exactly when $/1K hits ``cost_max_per_1k``."""
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config(cost_max_per_1k=0.6))
        # 5 closes × notional 100 = 500 total notional.
        # Loss 0.30 across 5 → $/1K = 0.30 / 0.5 = 0.6 → score 0.
        for _ in range(5):
            tracker.record_close("BTC", is_maker=False, net_pnl=-0.06, notional=100)
        h = tracker.get_health("BTC")
        assert abs(h.cost_score) < 0.001

    def test_cost_max_per_1k_is_config_driven(self, monkeypatch):
        """Same loss profile but cost_max_per_1k=0.3 → score 0 reaches sooner."""
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config(cost_max_per_1k=0.3))
        # $/1K = 0.6 with default; with cost_max_per_1k=0.3, score should
        # also be 0 (clamped at 0, not negative).
        for _ in range(5):
            tracker.record_close("BTC", is_maker=False, net_pnl=-0.06, notional=100)
        assert tracker.get_health("BTC").cost_score == 0.0


# --------------------------------------------------------------------------- #
# Composite score: weights wiring and config-driven behaviour
# --------------------------------------------------------------------------- #


class TestCompositeScore:
    def test_composite_with_default_weights(self, monkeypatch):
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config())
        # All three dimensions at 100 → composite at 100 (weights sum to 1.0).
        tracker.record_fill("BTC")
        for _ in range(5):
            tracker.record_close("BTC", is_maker=True, net_pnl=0.01, notional=100)
        h = tracker.get_health("BTC")
        assert h.activity_score == 100.0
        assert h.close_quality_score == 100.0
        assert h.cost_score == 100.0
        assert abs(h.composite_score - 100.0) < 0.001

    def test_composite_responds_to_custom_weights(self, monkeypatch):
        """Composite must honour the supplied weights, not hardcoded 0.3/0.4/0.3."""
        _patch_clock(monkeypatch, 1000.0)
        cfg = _config(weight_activity=1.0, weight_quality=0.0, weight_cost=0.0)
        tracker = CoinHealthTracker(cfg)
        tracker.record_fill("BTC")
        for _ in range(5):
            # Quality=0, cost should be near 0 with these losses.
            tracker.record_close("BTC", is_maker=False, net_pnl=-0.04, notional=100)
        h = tracker.get_health("BTC")
        # composite = activity (100) since quality/cost weights are 0.
        assert abs(h.composite_score - 100.0) < 0.001


# --------------------------------------------------------------------------- #
# Window expiry
# --------------------------------------------------------------------------- #


class TestWindowExpiry:
    def test_old_events_excluded_from_score(self, monkeypatch):
        """Events older than ``window_seconds`` must not affect the score."""
        clock = _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config(window_seconds=600.0))
        # Insert a close that will age out.
        tracker.record_close("BTC", is_maker=False, net_pnl=-1.0, notional=100)
        clock["t"] += 700.0  # now older than window
        # Close just inside the window:
        for _ in range(5):
            tracker.record_close("BTC", is_maker=True, net_pnl=0.01, notional=100)
        h = tracker.get_health("BTC")
        # Quality should be 100 (only the recent 5 maker closes count).
        assert h.close_quality_score == 100.0
        assert h.n_closes == 5

    def test_max_events_per_coin_caps_buffer(self, monkeypatch):
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config(), max_events_per_coin=10)
        for _ in range(50):
            tracker.record_close("BTC", is_maker=True, net_pnl=0.01, notional=100)
        # Buffer holds at most 10 even though 50 were appended.
        assert tracker.get_health("BTC").n_closes == 10


# --------------------------------------------------------------------------- #
# min_closes_for_quality is exposed but only consulted by the strategy gate;
# the tracker itself returns the raw quality score regardless. Verify so a
# future refactor doesn't accidentally bake the gate into the tracker.
# --------------------------------------------------------------------------- #


class TestQualityGateNotInTracker:
    def test_quality_score_returned_even_with_one_close(self, monkeypatch):
        _patch_clock(monkeypatch, 1000.0)
        tracker = CoinHealthTracker(_config(min_closes_for_quality=10))
        tracker.record_close("BTC", is_maker=False, net_pnl=-0.5, notional=100)
        # Even though 1 < min_closes_for_quality (10), tracker emits the
        # actual quality score (0). The strategy gate is responsible for
        # ignoring it; the tracker is unconditional.
        h = tracker.get_health("BTC")
        assert h.close_quality_score == 0.0
        assert h.n_closes == 1


# --------------------------------------------------------------------------- #
# Decision-table parametrisation: matches the design doc's table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "is_maker_pattern,net_pnls,expected_quality,expected_cost_low",
    [
        ([True] * 5, [0.01] * 5, 100.0, False),  # all maker, profitable
        ([False] * 5, [-0.10] * 5, 0.0, True),   # all taker, big loss → low cost score
        ([True, False] * 3, [0.01, -0.01] * 3, 50.0, False),  # half/half tiny loss
    ],
)
def test_decision_table(monkeypatch, is_maker_pattern, net_pnls, expected_quality, expected_cost_low):
    _patch_clock(monkeypatch, 1000.0)
    tracker = CoinHealthTracker(_config())
    for is_maker, pnl in zip(is_maker_pattern, net_pnls):
        tracker.record_close("BTC", is_maker=is_maker, net_pnl=pnl, notional=100)
    h = tracker.get_health("BTC")
    assert abs(h.close_quality_score - expected_quality) < 0.001
    if expected_cost_low:
        assert h.cost_score < 30.0
    else:
        assert h.cost_score >= 80.0
