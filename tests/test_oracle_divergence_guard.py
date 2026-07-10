"""Tests for OracleDivergenceGuard — oracle divergence / momentum gates.

Follows the test_imbalance_guard.py approach: callbacks are invoked
directly (no real WS), ``time`` is patched inside the guard module, and
the order tracker is a Mock.

The guard arms a coin only after it observes the oracle *value change*
while ticking (a lone snapshot of a frozen, market-closed oracle must
not arm it). ``_arm`` brings a coin to the armed/fresh state.
"""

from unittest.mock import MagicMock, patch

from ws.oracle_divergence_guard import OracleDivergenceGuard, _NEUTRAL


def _ctx(px):
    return {"oraclePx": str(px)}


def _levels(bid, ask, sz="1"):
    return [
        [{"px": str(bid), "sz": sz}],
        [{"px": str(ask), "sz": sz}],
    ]


def _make_guard(**kwargs):
    tracker = MagicMock()
    defaults = dict(
        divergence_threshold_bps=5.0,
        momentum_cap_pin_bps=80.0,
        momentum_min_step_bps=5.0,
        momentum_consecutive=3,
        stale_ttl_seconds=30.0,
        block_seconds=10.0,
        min_cancel_interval=0.0,  # disable rate limiting unless a test opts in
    )
    defaults.update(kwargs)
    guard = OracleDivergenceGuard(tracker, **defaults)
    return guard, tracker


class _Clock:
    """Controllable monotonic clock for the guard module."""

    def __init__(self, start=1000.0):
        self.t = start

    def monotonic(self):
        return self.t


def _patched(guard_clock):
    """Patch time.monotonic inside the guard module only."""
    mock_time = MagicMock()
    mock_time.monotonic.side_effect = guard_clock.monotonic
    return patch("ws.oracle_divergence_guard.time", mock_time)


def _arm(guard, coin, clock, px):
    """Arm the guard for ``coin`` at ``px`` (fresh).

    Requires an observed value change, so uses two ticks: a first sighting
    ~0.1bps below ``px`` (well under momentum_min_step) then ``px`` itself,
    which stamps freshness. Leaves ``_last_oracle_px == px`` and the coin
    within the staleness window, with momentum counters reset.
    """
    guard.on_asset_ctx_update(coin, _ctx(px * (1 - 1e-5)))  # first sighting
    clock.t += 3
    guard.on_asset_ctx_update(coin, _ctx(px))               # change -> arms
    clock.t += 3
    guard.order_tracker.reset_mock()  # discard any arming-phase interaction


class TestArming:
    def test_lone_snapshot_does_not_arm(self):
        # Regression: a single frozen-oracle snapshot at startup during
        # market close must NOT arm the guard — a floating book is not a
        # divergence.
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            guard.on_asset_ctx_update("xyz:NVDA", _ctx(100.0))  # lone snapshot
            clock.t += 1
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))  # big divergence
        tracker.cancel_orders_by_side.assert_not_called()
        assert guard._div_state.get("xyz:NVDA", _NEUTRAL) == _NEUTRAL

    def test_frozen_oracle_never_arms(self):
        # Same value pushed repeatedly (market closed) never arms.
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            for _ in range(6):
                guard.on_asset_ctx_update("xyz:NVDA", _ctx(100.0))
                clock.t += 3
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
        tracker.cancel_orders_by_side.assert_not_called()

    def test_arms_after_observed_change(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
        tracker.cancel_orders_by_side.assert_called_once_with("xyz:NVDA", "A")


class TestDivergenceGate:
    def test_oracle_above_mid_cancels_sell_side(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            # mid 99.01 vs oracle 100 -> ~100bps divergence, oracle above
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
        tracker.cancel_orders_by_side.assert_called_once_with("xyz:NVDA", "A")

    def test_oracle_below_mid_cancels_buy_side(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 99.0)
            guard.on_l2_update("xyz:NVDA", _levels(99.99, 100.01))
        tracker.cancel_orders_by_side.assert_called_once_with("xyz:NVDA", "B")

    def test_below_threshold_no_fire(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            # mid 100.0 vs oracle 100 -> ~0bps
            guard.on_l2_update("xyz:NVDA", _levels(99.99, 100.01))
        tracker.cancel_orders_by_side.assert_not_called()

    def test_exact_threshold_no_fire(self):
        # Contract: fire on strictly greater than threshold (> not >=)
        guard, tracker = _make_guard(divergence_threshold_bps=10.0)
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            # mid 99.9 -> div = (100-99.9)/99.9*1e4 = 10.01bps > 10 -> fires;
            # use a mid giving exactly 10bps to confirm no fire at boundary
            # div = 10 exactly -> mid = 100/(1+10/1e4)
            mid = 100.0 / (1 + 10.0 / 1e4)
            guard.on_l2_update("xyz:NVDA", _levels(mid, mid))
        tracker.cancel_orders_by_side.assert_not_called()

    def test_zero_threshold_disables_divergence_gate(self):
        guard, tracker = _make_guard(divergence_threshold_bps=0.0)
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
        tracker.cancel_orders_by_side.assert_not_called()

    def test_persisting_divergence_fires_once(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
            clock.t += 1
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
            clock.t += 1
            guard.on_l2_update("xyz:NVDA", _levels(99.01, 99.03))
        assert tracker.cancel_orders_by_side.call_count == 1

    def test_rearms_after_neutral_return(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))      # fire 1
            clock.t += 1
            guard.on_l2_update("xyz:NVDA", _levels(99.99, 100.01))    # neutral
            clock.t += 1
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))      # fire 2
        assert tracker.cancel_orders_by_side.call_count == 2

    def test_min_cancel_interval_skips_but_counts(self):
        guard, tracker = _make_guard(min_cancel_interval=5.0)
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))      # fire, cancels
            clock.t += 1
            guard.on_l2_update("xyz:NVDA", _levels(99.99, 100.01))    # neutral
            clock.t += 1
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))      # fire, rate-limited
        assert tracker.cancel_orders_by_side.call_count == 1
        assert guard.stats["skipped_rate_limit"] == 1

    def test_divergence_fire_blocks_that_side(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
            assert guard.get_blocked_sides("xyz:NVDA") == {"A"}
            clock.t += 11  # past block_seconds
            assert guard.get_blocked_sides("xyz:NVDA") == set()


class TestMomentumGate:
    def test_cap_pin_fires_both_sides(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.on_asset_ctx_update("xyz:NVDA", _ctx(101.0))  # +100bps >= 80
            assert guard.get_blocked_sides("xyz:NVDA") == {"B", "A"}
        # single both-sides cancel call, not per-side
        tracker.cancel_all_orders_for_coin.assert_called_once_with(
            "xyz:NVDA", reason="oracle_momentum")
        tracker.cancel_orders_by_side.assert_not_called()

    def test_zero_cap_pin_disables_pin_detection(self):
        guard, tracker = _make_guard(momentum_cap_pin_bps=0.0)
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.on_asset_ctx_update("xyz:NVDA", _ctx(101.0))
        # +100bps counts as one consecutive step (>= min_step) but no pin fire
        tracker.cancel_all_orders_for_coin.assert_not_called()

    def test_consecutive_same_direction_fires(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            px = 100.0
            for _ in range(3):
                clock.t += 3
                px *= 1.001  # +10bps steps
                guard.on_asset_ctx_update("xyz:NVDA", _ctx(px))
            assert guard.get_blocked_sides("xyz:NVDA") == {"B", "A"}
        assert tracker.cancel_all_orders_for_coin.call_count == 1

    def test_direction_flip_resets_count(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            for px in (100.1, 100.0, 100.1, 100.0):  # alternating ~10bps
                clock.t += 3
                guard.on_asset_ctx_update("xyz:NVDA", _ctx(px))
        tracker.cancel_all_orders_for_coin.assert_not_called()

    def test_small_step_resets_count(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            # two big up-steps, a tiny step (resets), two more big steps -> no fire
            for px in (100.1, 100.2, 100.2005, 100.3, 100.4):
                clock.t += 3
                guard.on_asset_ctx_update("xyz:NVDA", _ctx(px))
        tracker.cancel_all_orders_for_coin.assert_not_called()

    def test_same_value_push_is_ignored(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            updates_before = guard.stats["oracle_updates"]
            for _ in range(5):
                clock.t += 3
                guard.on_asset_ctx_update("xyz:NVDA", _ctx(100.0))
        tracker.cancel_all_orders_for_coin.assert_not_called()
        # same-value pushes are not counted as steps
        assert guard.stats["oracle_updates"] == updates_before

    def test_zero_consecutive_disables_consecutive_detection(self):
        guard, tracker = _make_guard(momentum_consecutive=0)
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            px = 100.0
            for _ in range(5):
                clock.t += 3
                px *= 1.001
                guard.on_asset_ctx_update("xyz:NVDA", _ctx(px))
        tracker.cancel_all_orders_for_coin.assert_not_called()


class TestMarketCloseGate:
    def test_armed_then_stale_disarms_divergence(self):
        guard, tracker = _make_guard(stale_ttl_seconds=30.0)
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            clock.t += 40  # oracle value unchanged for > TTL
            guard.on_l2_update("xyz:NVDA", _levels(90.0, 90.02))  # huge float
        tracker.cancel_orders_by_side.assert_not_called()
        assert guard._div_state.get("xyz:NVDA", _NEUTRAL) == _NEUTRAL

    def test_stale_recovery_rebaselines_without_firing(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            clock.t += 40
            # Post-open gap: +500bps step must NOT count as momentum
            guard.on_asset_ctx_update("xyz:NVDA", _ctx(105.0))
        tracker.cancel_orders_by_side.assert_not_called()

    def test_next_step_after_recovery_is_normal(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            clock.t += 40
            guard.on_asset_ctx_update("xyz:NVDA", _ctx(105.0))   # re-baseline
            clock.t += 3
            guard.on_asset_ctx_update("xyz:NVDA", _ctx(110.0))   # +476bps -> cap pin
        assert tracker.cancel_all_orders_for_coin.call_count == 1  # both sides, one call

    def test_flat_oracle_decays_to_stale_despite_pushes(self):
        # Same-value pushes must not renew freshness
        guard, tracker = _make_guard(stale_ttl_seconds=30.0)
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            for _ in range(15):
                clock.t += 3
                guard.on_asset_ctx_update("xyz:NVDA", _ctx(100.0))
            # 45s elapsed, oracle value never changed since arming -> stale
            guard.on_l2_update("xyz:NVDA", _levels(90.0, 90.02))
        tracker.cancel_orders_by_side.assert_not_called()


class TestFailSafe:
    def test_missing_oracle_px(self):
        guard, tracker = _make_guard()
        guard.on_asset_ctx_update("xyz:NVDA", {})
        guard.on_asset_ctx_update("xyz:NVDA", {"oraclePx": None})
        tracker.cancel_orders_by_side.assert_not_called()
        assert guard.stats["errors"] == 0

    def test_unparseable_or_nonpositive_oracle_px(self):
        guard, tracker = _make_guard()
        guard.on_asset_ctx_update("xyz:NVDA", {"oraclePx": "abc"})
        guard.on_asset_ctx_update("xyz:NVDA", {"oraclePx": "0"})
        guard.on_asset_ctx_update("xyz:NVDA", {"oraclePx": "-1"})
        guard.on_asset_ctx_update("xyz:NVDA", "not-a-dict")
        tracker.cancel_orders_by_side.assert_not_called()
        assert guard.stats["errors"] == 0

    def test_no_mid_yet_divergence_skipped(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)  # armed but no l2 mid seen
            guard.on_asset_ctx_update("xyz:NVDA", _ctx(100.01))  # tiny step
        tracker.cancel_orders_by_side.assert_not_called()

    def test_empty_or_one_sided_levels(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("xyz:NVDA", [])
        guard.on_l2_update("xyz:NVDA", [[]])
        guard.on_l2_update("xyz:NVDA", [[], [{"px": "100", "sz": "1"}]])
        tracker.cancel_orders_by_side.assert_not_called()
        assert guard.stats["errors"] == 0

    def test_l2_before_any_ctx_is_noop(self):
        guard, tracker = _make_guard()
        guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
        tracker.cancel_orders_by_side.assert_not_called()


class TestLifecycle:
    def test_stopped_guard_ignores_callbacks(self):
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.stop()
            clock.t += 1
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
            guard.on_asset_ctx_update("xyz:NVDA", _ctx(101.0))
        tracker.cancel_orders_by_side.assert_not_called()
        assert guard.is_running is False

    def test_summary_logs_and_resets(self, caplog):
        import logging
        guard, tracker = _make_guard()
        clock = _Clock()
        with _patched(clock):
            _arm(guard, "xyz:NVDA", clock, 100.0)
            guard.on_l2_update("xyz:NVDA", _levels(99.0, 99.02))
            guard._last_summary_time = clock.t - 400  # force interval elapsed
            with caplog.at_level(logging.INFO, logger="ws.oracle_divergence_guard"):
                guard.maybe_log_summary()
        assert any("[oracle-guard] Summary" in r.message for r in caplog.records)
        assert guard.stats["max_divergence"] == {}  # reset after summary

    def test_stats_shape(self):
        guard, _ = _make_guard()
        stats = guard.stats
        for key in ("running", "oracle_updates", "divergence_fires",
                    "momentum_fires", "cancels_triggered",
                    "skipped_rate_limit", "errors", "max_divergence"):
            assert key in stats
