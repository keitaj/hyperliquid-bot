"""Integration tests: ``OrderManager`` ↔ ``OrderRejectionTracker``.

Pin the contract that:

1. With **no tracker** registered, the legacy ``logger.error`` line is
   emitted exactly as before. This is the load-bearing back-compat
   guarantee — any deployment that doesn't opt in must see byte-identical
   behaviour.
2. With a tracker registered, every rejection is routed through it
   (single order *and* bulk paths), no duplicate ERROR is emitted, and
   the tracker counts the rejection per coin.

The tests intentionally reach into ``OrderManager._place_order`` /
``_bulk_place_orders_with_builder`` via the public ``bulk_place_orders``
shim and a single-order helper because both paths share the rejection
hook and we want regression coverage on both.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from order_manager import Order, OrderManager, OrderSide, OrderStatus
from order_rejection_tracker import OrderRejectionTracker


_POST_ONLY = (
    "Post only order would have immediately matched, "
    "bbo was 98.73@98.758. asset=170005"
)


@pytest.fixture(autouse=True)
def _bypass_api_wrapper():
    with patch('order_manager.api_wrapper') as mock_wrapper:
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        yield mock_wrapper


def _make_order_manager():
    exchange = MagicMock()
    info = MagicMock()
    return OrderManager(exchange, info, '0xabc')


def _make_order(coin='xyz:NVDA', side=OrderSide.BUY):
    return Order(
        id=None, coin=coin, side=side, size=0.1, price=100.0,
        order_type={"limit": {"tif": "Alo"}}, reduce_only=False,
    )


# --------------------------------------------------------------------- #
# Single-order rejection path
# --------------------------------------------------------------------- #


class TestSingleRejectionWithoutTracker:
    """Legacy behaviour: ERROR-level ``logger.error`` line is emitted."""

    def test_single_rejection_logs_error(self, caplog):
        mgr = _make_order_manager()
        mgr.exchange.order.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': [{'error': _POST_ONLY}]}},
        }

        with caplog.at_level(logging.DEBUG, logger='order_manager'):
            result = mgr._place_order(_make_order())

        assert result is None
        # Legacy line: exactly one ERROR with "Order rejected:" prefix.
        errs = [
            r for r in caplog.records
            if r.levelno == logging.ERROR
            and r.message.startswith("Order rejected: ")
        ]
        assert len(errs) == 1
        assert _POST_ONLY in errs[0].message


class TestSingleRejectionWithTracker:
    """With tracker: routine match downgraded, counter incremented."""

    def test_single_rejection_routed_through_tracker(self, caplog):
        mgr = _make_order_manager()
        tracker = OrderRejectionTracker(
            routine_log_level='warning', summary_interval=0,
        )
        mgr.set_rejection_tracker(tracker)

        mgr.exchange.order.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': [{'error': _POST_ONLY}]}},
        }

        with caplog.at_level(logging.DEBUG):
            result = mgr._place_order(_make_order(coin='xyz:NVDA'))

        assert result is None
        # Tracker recorded the rejection
        snap = tracker.get_stats_snapshot()
        assert snap['post_only_match']['xyz:NVDA'] == 1

        # No legacy ERROR line should have been emitted by order_manager.
        legacy_errs = [
            r for r in caplog.records
            if r.name == 'order_manager'
            and r.levelno == logging.ERROR
            and r.message.startswith("Order rejected:")
        ]
        assert legacy_errs == [], (
            "order_manager must defer to tracker, not double-log"
        )

        # Tracker emits its own line at the configured WARNING level.
        tracker_lines = [
            r for r in caplog.records
            if r.name == 'order_rejection_tracker'
            and "[reject:post_only_match]" in r.message
        ]
        assert len(tracker_lines) == 1
        assert tracker_lines[0].levelno == logging.WARNING


class TestUnknownPatternRouted:
    """Unknown text is still surfaced at ERROR via the tracker path."""

    def test_unknown_text_via_tracker_logs_error(self, caplog):
        mgr = _make_order_manager()
        tracker = OrderRejectionTracker(
            routine_log_level='info', summary_interval=0,
        )
        mgr.set_rejection_tracker(tracker)

        unknown = "Some genuinely new exchange rejection"
        mgr.exchange.order.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': [{'error': unknown}]}},
        }

        with caplog.at_level(logging.DEBUG):
            mgr._place_order(_make_order(coin='xyz:NVDA'))

        # Tracker logs ERROR for unknown patterns regardless of configured
        # routine level; the unknown counter is incremented.
        assert tracker.get_unknown_count() == 1
        errs = [
            r for r in caplog.records
            if r.name == 'order_rejection_tracker'
            and r.levelno == logging.ERROR
        ]
        assert len(errs) == 1


# --------------------------------------------------------------------- #
# Bulk-order rejection path
# --------------------------------------------------------------------- #


class TestBulkRejectionWithoutTracker:
    def test_bulk_rejection_logs_error_with_index(self, caplog):
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': [
                {'resting': {'oid': 100}},
                {'error': _POST_ONLY},
            ]}},
        }

        orders = [_make_order(coin='xyz:NVDA'), _make_order(coin='xyz:TSLA')]
        with caplog.at_level(logging.DEBUG, logger='order_manager'):
            results = mgr.bulk_place_orders(orders)

        assert results[0] is not None
        assert results[1] is None
        assert orders[1].status == OrderStatus.REJECTED

        errs = [
            r for r in caplog.records
            if r.levelno == logging.ERROR
            and "Bulk order [1] rejected" in r.message
        ]
        assert len(errs) == 1


class TestBulkRejectionWithTracker:
    def test_bulk_rejection_routed_per_coin(self, caplog):
        mgr = _make_order_manager()
        tracker = OrderRejectionTracker(
            routine_log_level='warning', summary_interval=0,
        )
        mgr.set_rejection_tracker(tracker)

        mgr.exchange.bulk_orders.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': [
                {'error': _POST_ONLY},          # NVDA rejected
                {'resting': {'oid': 200}},      # TSLA placed
                {'error': _POST_ONLY},          # SP500 rejected
            ]}},
        }

        orders = [
            _make_order(coin='xyz:NVDA'),
            _make_order(coin='xyz:TSLA'),
            _make_order(coin='xyz:SP500'),
        ]
        with caplog.at_level(logging.DEBUG):
            results = mgr.bulk_place_orders(orders)

        assert results[0] is None
        assert results[1] is not None and results[1].id == 200
        assert results[2] is None

        snap = tracker.get_stats_snapshot()
        # Each rejection counted under its own coin
        assert snap['post_only_match']['xyz:NVDA'] == 1
        assert snap['post_only_match']['xyz:SP500'] == 1
        assert 'xyz:TSLA' not in snap['post_only_match']

        # No legacy ``Bulk order [N] rejected`` ERROR from order_manager
        legacy = [
            r for r in caplog.records
            if r.name == 'order_manager'
            and r.levelno == logging.ERROR
            and 'Bulk order' in r.message
        ]
        assert legacy == []
