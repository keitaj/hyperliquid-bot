"""Contract tests for ``strategies.mm_order_tracker.OrderTracker``.

The most load-bearing invariant captured here:

    Close orders never enter ``OrderTracker._tracked_orders``.

Close orders are owned by ``MMPositionCloser._open_positions``;
``OrderTracker`` is for entry quotes only. Several websocket guards
(``ImbalanceGuard``, ``BboGuard``, ``BboVelocityGuard``,
``FillFeed``) call into the tracker to cancel orders. If a close
order ever leaks into ``_tracked_orders``, those guards would start
cancelling close-side quotes, which would cause the bot to fall back
to taker close on a reduce-only timeout instead of letting the maker
close fill -- exactly the failure mode this contract is meant to
prevent.
"""

from unittest.mock import MagicMock

import pytest

from strategies.mm_order_tracker import OrderTracker


class TestCloseOrderInvariant:
    """Close orders must not appear in ``_tracked_orders``."""

    def test_cancel_by_side_does_not_touch_unregistered_close_oid(self) -> None:
        """A close oid that lives only in MMPositionCloser is never cancelled
        by guards that route through ``OrderTracker.cancel_orders_by_side``.
        """
        order_manager = MagicMock()
        order_manager.bulk_cancel_orders.return_value = 0
        tracker = OrderTracker(order_manager, refresh_interval_seconds=60, max_open_orders=4)

        # Strategy registers two entry quotes (one each side).
        tracker.record_order("xyz:NVDA", oid=1001, side="B")
        tracker.record_order("xyz:NVDA", oid=1002, side="A")

        # MMPositionCloser would track its close oid in *its own* map.
        # We simulate that by simply not calling record_order for it.
        close_oid_in_position_closer = 5000

        # Both guard-driven cancel paths run.
        tracker.cancel_orders_by_side("xyz:NVDA", "B")
        tracker.cancel_orders_by_side("xyz:NVDA", "A")

        cancelled_oids = {
            req["oid"]
            for call in order_manager.bulk_cancel_orders.call_args_list
            for req in call.args[0]
        }
        assert cancelled_oids == {1001, 1002}
        assert close_oid_in_position_closer not in cancelled_oids

    def test_cancel_all_for_coin_does_not_touch_unregistered_close_oid(self) -> None:
        """The fill-feed cleanup path ``cancel_all_orders_for_coin`` likewise
        only cancels what was registered via ``record_order``.
        """
        order_manager = MagicMock()
        order_manager.bulk_cancel_orders.return_value = 0
        tracker = OrderTracker(order_manager, refresh_interval_seconds=60, max_open_orders=4)

        tracker.record_order("xyz:NVDA", oid=2001, side="B")
        close_oid_in_position_closer = 5001

        tracker.cancel_all_orders_for_coin("xyz:NVDA")

        cancelled_oids = {
            req["oid"]
            for call in order_manager.bulk_cancel_orders.call_args_list
            for req in call.args[0]
        }
        assert cancelled_oids == {2001}
        assert close_oid_in_position_closer not in cancelled_oids

    def test_get_order_count_excludes_unregistered_close_oid(self) -> None:
        """``get_order_count`` (used for max-open-orders gating) must not
        count close orders, otherwise positions with a live close maker
        would burn entry budget.
        """
        order_manager = MagicMock()
        tracker = OrderTracker(order_manager, refresh_interval_seconds=60, max_open_orders=4)

        tracker.record_order("xyz:NVDA", oid=3001, side="B")
        tracker.record_order("xyz:NVDA", oid=3002, side="A")
        # close_oid 5002 lives only in MMPositionCloser -- not registered here.

        assert tracker.get_order_count("xyz:NVDA") == 2


class TestRecordOrderBackwardCompat:
    """``record_order`` must accept calls without a price for backward compat."""

    def test_record_order_without_price(self) -> None:
        """Legacy callers omit ``price``; the tracker must still register the order."""
        order_manager = MagicMock()
        tracker = OrderTracker(order_manager, refresh_interval_seconds=60, max_open_orders=4)

        tracker.record_order("BTC", oid=10, side="B")

        assert tracker.get_order_count("BTC") == 1
        assert tracker.get_open_sides("BTC") == {"B"}

    def test_record_order_with_price(self) -> None:
        """New callers pass ``price`` to enable tolerance-based keep."""
        order_manager = MagicMock()
        tracker = OrderTracker(order_manager, refresh_interval_seconds=60, max_open_orders=4)

        tracker.record_order("BTC", oid=11, side="A", price=42_000.5)

        assert tracker.get_order_count("BTC") == 1
        assert tracker.get_open_sides("BTC") == {"A"}


class TestRefreshOrdersWithTolerance:
    """Behaviour of ``refresh_orders_with_tolerance``.

    Time is controlled by monkeypatching ``time.monotonic`` so order ages
    are deterministic regardless of how fast the test runs.
    """

    def _make_tracker(self, monkeypatch, now: float = 1000.0):
        """Helper: tracker with a controllable monotonic clock."""
        from strategies import mm_order_tracker as tracker_mod

        clock = {"t": now}
        monkeypatch.setattr(tracker_mod.time, "monotonic", lambda: clock["t"])

        order_manager = MagicMock()
        order_manager.bulk_cancel_orders.return_value = 0
        tracker = OrderTracker(order_manager, refresh_interval_seconds=30, max_open_orders=4)
        return tracker, order_manager, clock

    def test_keeps_order_within_tolerance_and_age(self, monkeypatch) -> None:
        """Order whose price drift is within tolerance and age < max_age is kept."""
        tracker, order_manager, clock = self._make_tracker(monkeypatch)
        tracker.record_order("BTC", oid=1, side="B", price=100.0)
        order_manager.get_open_orders = MagicMock(return_value=[{"oid": 1}])

        # Age 5s, drift 5bp ((100.05 - 100) / 100 * 1e4 = 5)
        clock["t"] += 5.0
        result = tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 100.05, "A": 101.0},
            tolerance_bp=10.0,
            max_age_seconds=120.0,
        )

        assert result == {"kept": 1, "cancelled_drift": 0, "cancelled_age": 0}
        assert tracker.get_order_count("BTC") == 1
        order_manager.bulk_cancel_orders.assert_not_called()

    def test_cancels_when_drift_exceeds_tolerance(self, monkeypatch) -> None:
        """Drift > tolerance triggers immediate cancel even when young."""
        tracker, order_manager, clock = self._make_tracker(monkeypatch)
        tracker.record_order("BTC", oid=2, side="A", price=100.0)
        order_manager.get_open_orders = MagicMock(return_value=[{"oid": 2}])

        # Age 1s, drift 100bp ((101 - 100) / 100 * 1e4 = 100)
        clock["t"] += 1.0
        result = tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 99.0, "A": 101.0},
            tolerance_bp=10.0,
            max_age_seconds=120.0,
        )

        assert result == {"kept": 0, "cancelled_drift": 1, "cancelled_age": 0}
        assert tracker.get_order_count("BTC") == 0
        order_manager.bulk_cancel_orders.assert_called_once()

    def test_cancels_when_age_exceeds_max_age(self, monkeypatch) -> None:
        """Even within tolerance, an order older than max_age is cancelled."""
        tracker, order_manager, clock = self._make_tracker(monkeypatch)
        tracker.record_order("BTC", oid=3, side="B", price=100.0)
        order_manager.get_open_orders = MagicMock(return_value=[{"oid": 3}])

        # Age 200s, no drift
        clock["t"] += 200.0
        result = tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 100.0, "A": 101.0},
            tolerance_bp=10.0,
            max_age_seconds=120.0,
        )

        assert result == {"kept": 0, "cancelled_drift": 0, "cancelled_age": 1}
        assert tracker.get_order_count("BTC") == 0

    def test_close_oid_is_never_cancelled(self, monkeypatch) -> None:
        """``close_oid`` is preserved regardless of drift or age."""
        tracker, order_manager, clock = self._make_tracker(monkeypatch)
        tracker.record_order("BTC", oid=4, side="B", price=100.0)
        order_manager.get_open_orders = MagicMock(return_value=[{"oid": 4}])

        # Both drift > tolerance AND age > max_age -- still must keep.
        clock["t"] += 500.0
        result = tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 90.0, "A": 110.0},
            tolerance_bp=1.0,
            max_age_seconds=60.0,
            close_oid=4,
        )

        assert tracker.get_order_count("BTC") == 1
        # close_oid is kept implicitly (not counted as kept since it bypasses
        # the tolerance evaluation entirely).
        assert result["cancelled_drift"] == 0
        assert result["cancelled_age"] == 0
        order_manager.bulk_cancel_orders.assert_not_called()

    def test_falls_back_to_age_when_price_unrecorded(self, monkeypatch) -> None:
        """Orders recorded without price (legacy/zero) fall back to age-only."""
        tracker, order_manager, clock = self._make_tracker(monkeypatch)
        tracker.record_order("BTC", oid=5, side="B")  # no price -> 0.0
        order_manager.get_open_orders = MagicMock(return_value=[{"oid": 5}])

        # Age below refresh_interval -> keep
        clock["t"] += 10.0
        result = tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 100.0, "A": 101.0},
            tolerance_bp=10.0,
            max_age_seconds=120.0,
        )
        assert tracker.get_order_count("BTC") == 1
        # Not counted as kept (it's the fallback path), but still in tracker.
        assert result["kept"] == 0
        order_manager.bulk_cancel_orders.assert_not_called()

        # Age >= refresh_interval -> cancel
        clock["t"] += 30.0
        tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 100.0, "A": 101.0},
            tolerance_bp=10.0,
            max_age_seconds=120.0,
        )
        assert tracker.get_order_count("BTC") == 0

    def test_drops_orders_no_longer_open_on_exchange(self, monkeypatch) -> None:
        """Orders absent from the exchange's open-orders list are dropped silently."""
        tracker, order_manager, _ = self._make_tracker(monkeypatch)
        tracker.record_order("BTC", oid=6, side="B", price=100.0)
        # Exchange reports no open orders -> our tracked oid 6 was filled or
        # cancelled out-of-band.
        order_manager.get_open_orders = MagicMock(return_value=[])

        result = tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 100.0, "A": 101.0},
            tolerance_bp=10.0,
            max_age_seconds=120.0,
        )

        assert tracker.get_order_count("BTC") == 0
        assert result == {"kept": 0, "cancelled_drift": 0, "cancelled_age": 0}
        order_manager.bulk_cancel_orders.assert_not_called()

    def test_partial_evaluation_when_only_one_side_has_ideal(self, monkeypatch) -> None:
        """Sides without an ideal price fall back to age-only; sides with one
        get tolerance evaluation."""
        tracker, order_manager, clock = self._make_tracker(monkeypatch)
        tracker.record_order("BTC", oid=7, side="B", price=100.0)
        tracker.record_order("BTC", oid=8, side="A", price=200.0)
        order_manager.get_open_orders = MagicMock(
            return_value=[{"oid": 7}, {"oid": 8}]
        )

        # Provide ideal only for the buy side; the ask side falls back to age.
        clock["t"] += 5.0
        tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 100.0},  # ask not provided
            tolerance_bp=5.0,
            max_age_seconds=120.0,
        )

        # Both kept: B due to tolerance, A due to age below refresh_interval.
        assert tracker.get_order_count("BTC") == 2

    def test_cumulative_stats_accumulate_across_calls(self, monkeypatch) -> None:
        """``get_refresh_stats`` reflects all calls cumulatively."""
        tracker, order_manager, clock = self._make_tracker(monkeypatch)
        tracker.record_order("BTC", oid=9, side="B", price=100.0)
        tracker.record_order("BTC", oid=10, side="A", price=101.0)
        order_manager.get_open_orders = MagicMock(
            return_value=[{"oid": 9}, {"oid": 10}]
        )

        # Cycle 1: both within tolerance.
        clock["t"] += 5.0
        tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 100.0, "A": 101.0},
            tolerance_bp=10.0,
            max_age_seconds=120.0,
        )

        stats = tracker.get_refresh_stats()
        assert stats["kept"] == 2
        assert stats["cancelled_drift"] == 0
        assert stats["cancelled_age"] == 0

        # Cycle 2: ideal moves -> both cancelled by drift.
        # (Re-register so they still exist after the first cycle's keep.)
        order_manager.get_open_orders = MagicMock(
            return_value=[{"oid": 9}, {"oid": 10}]
        )
        tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 110.0, "A": 91.0},
            tolerance_bp=1.0,
            max_age_seconds=120.0,
        )

        stats = tracker.get_refresh_stats()
        assert stats["kept"] == 2
        assert stats["cancelled_drift"] == 2

    def test_tolerance_zero_cancels_any_drift(self, monkeypatch) -> None:
        """``tolerance_bp == 0`` keeps only orders at exactly the ideal price."""
        tracker, order_manager, clock = self._make_tracker(monkeypatch)
        tracker.record_order("BTC", oid=20, side="B", price=100.0)
        tracker.record_order("BTC", oid=21, side="A", price=101.0)
        order_manager.get_open_orders = MagicMock(
            return_value=[{"oid": 20}, {"oid": 21}]
        )

        # Tiny drift on bid; ask exact.
        clock["t"] += 1.0
        result = tracker.refresh_orders_with_tolerance(
            "BTC",
            ideal_prices={"B": 100.001, "A": 101.0},
            tolerance_bp=0.0,
            max_age_seconds=120.0,
        )

        assert result["cancelled_drift"] == 1
        assert result["kept"] == 1
        assert tracker.get_open_sides("BTC") == {"A"}


class TestGetOpenSides:
    """``get_open_sides`` reflects currently tracked orders by side."""

    def test_empty_when_no_orders(self) -> None:
        order_manager = MagicMock()
        tracker = OrderTracker(order_manager, refresh_interval_seconds=30, max_open_orders=4)
        assert tracker.get_open_sides("BTC") == set()

    def test_reports_both_sides(self) -> None:
        order_manager = MagicMock()
        tracker = OrderTracker(order_manager, refresh_interval_seconds=30, max_open_orders=4)
        tracker.record_order("BTC", oid=1, side="B", price=100.0)
        tracker.record_order("BTC", oid=2, side="A", price=101.0)
        assert tracker.get_open_sides("BTC") == {"B", "A"}

    def test_isolated_per_coin(self) -> None:
        order_manager = MagicMock()
        tracker = OrderTracker(order_manager, refresh_interval_seconds=30, max_open_orders=4)
        tracker.record_order("BTC", oid=1, side="B", price=100.0)
        tracker.record_order("ETH", oid=2, side="A", price=2_000.0)
        assert tracker.get_open_sides("BTC") == {"B"}
        assert tracker.get_open_sides("ETH") == {"A"}
        assert tracker.get_open_sides("SOL") == set()


@pytest.mark.parametrize(
    "tolerance_bp,age_seconds,expect_keep",
    [
        (5.0, 10.0, True),    # within tolerance, young
        (5.0, 200.0, False),  # within tolerance, too old (max_age cancel)
        (1.0, 10.0, False),   # outside tolerance, young (drift cancel)
        (0.0, 10.0, False),   # zero tolerance with non-zero drift
    ],
)
def test_refresh_decision_table(monkeypatch, tolerance_bp, age_seconds, expect_keep) -> None:
    """Compact truth table verifying the keep/cancel decision."""
    from strategies import mm_order_tracker as tracker_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(tracker_mod.time, "monotonic", lambda: clock["t"])

    order_manager = MagicMock()
    order_manager.bulk_cancel_orders.return_value = 0
    order_manager.get_open_orders = MagicMock(return_value=[{"oid": 1}])
    tracker = OrderTracker(order_manager, refresh_interval_seconds=30, max_open_orders=4)

    tracker.record_order("BTC", oid=1, side="B", price=100.0)
    clock["t"] += age_seconds

    # Drift = 2bp (constant): ((100.02 - 100) / 100) * 1e4 = 2
    tracker.refresh_orders_with_tolerance(
        "BTC",
        ideal_prices={"B": 100.02, "A": 101.0},
        tolerance_bp=tolerance_bp,
        max_age_seconds=120.0,
    )

    if expect_keep:
        assert tracker.get_order_count("BTC") == 1
    else:
        assert tracker.get_order_count("BTC") == 0
