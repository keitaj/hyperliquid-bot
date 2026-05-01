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
