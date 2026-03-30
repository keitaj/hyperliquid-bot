"""Tests for bulk cancel functionality."""

from unittest.mock import MagicMock, patch
import pytest

from order_manager import OrderManager, Order, OrderSide


@pytest.fixture(autouse=True)
def _bypass_api_wrapper():
    with patch('order_manager.api_wrapper') as mock_wrapper:
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        yield mock_wrapper


def _make_order_manager():
    exchange = MagicMock()
    info = MagicMock()
    return OrderManager(exchange, info, '0xabc')


class TestBulkCancelOrders:

    def test_bulk_cancel_success(self):
        """All orders cancelled successfully."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_cancel.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': ['success', 'success']}},
        }

        requests = [
            {'coin': 'BTC', 'oid': 100},
            {'coin': 'ETH', 'oid': 200},
        ]
        result = mgr.bulk_cancel_orders(requests)

        assert result == 2
        mgr.exchange.bulk_cancel.assert_called_once_with(requests)

    def test_bulk_cancel_partial(self):
        """Some cancels fail."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_cancel.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': ['success', 'failed']}},
        }

        result = mgr.bulk_cancel_orders([
            {'coin': 'BTC', 'oid': 100},
            {'coin': 'ETH', 'oid': 200},
        ])

        assert result == 1

    def test_bulk_cancel_empty(self):
        """Empty list returns 0 without API call."""
        mgr = _make_order_manager()

        result = mgr.bulk_cancel_orders([])

        assert result == 0
        mgr.exchange.bulk_cancel.assert_not_called()

    def test_bulk_cancel_updates_active_orders(self):
        """Successfully cancelled orders are removed from active_orders."""
        mgr = _make_order_manager()
        order = Order(
            id=100, coin='BTC', side=OrderSide.BUY,
            size=0.1, price=50000.0,
            order_type={'limit': {'tif': 'Gtc'}},
        )
        mgr.active_orders[100] = order

        mgr.exchange.bulk_cancel.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': ['success']}},
        }

        mgr.bulk_cancel_orders([{'coin': 'BTC', 'oid': 100}])

        assert 100 not in mgr.active_orders

    def test_bulk_cancel_api_error(self):
        """API exception returns 0."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_cancel.side_effect = Exception("network error")

        result = mgr.bulk_cancel_orders([{'coin': 'BTC', 'oid': 100}])

        assert result == 0

    def test_bulk_cancel_bad_response(self):
        """Non-ok status returns 0."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_cancel.return_value = {'status': 'error'}

        result = mgr.bulk_cancel_orders([{'coin': 'BTC', 'oid': 100}])

        assert result == 0


class TestCancelAllOrdersBatch:

    def test_cancel_all_uses_bulk(self):
        """cancel_all_orders should use bulk_cancel instead of individual calls."""
        mgr = _make_order_manager()
        mgr.info.open_orders.return_value = [
            {'coin': 'BTC', 'oid': '100'},
            {'coin': 'ETH', 'oid': '200'},
            {'coin': 'BTC', 'oid': '300'},
        ]
        mgr.exchange.bulk_cancel.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': ['success', 'success', 'success']}},
        }

        result = mgr.cancel_all_orders()

        assert result == 3
        # Single bulk_cancel call, NOT 3 individual cancel calls
        mgr.exchange.bulk_cancel.assert_called_once()
        mgr.exchange.cancel.assert_not_called()

    def test_cancel_all_filtered_by_coin(self):
        """cancel_all_orders with coin filter only cancels matching orders."""
        mgr = _make_order_manager()
        mgr.info.open_orders.return_value = [
            {'coin': 'BTC', 'oid': '100'},
            {'coin': 'ETH', 'oid': '200'},
            {'coin': 'BTC', 'oid': '300'},
        ]
        mgr.exchange.bulk_cancel.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': ['success', 'success']}},
        }

        result = mgr.cancel_all_orders(coin='BTC')

        assert result == 2
        call_args = mgr.exchange.bulk_cancel.call_args[0][0]
        assert all(r['coin'] == 'BTC' for r in call_args)

    def test_cancel_all_no_orders(self):
        """No open orders returns 0 without calling bulk_cancel."""
        mgr = _make_order_manager()
        mgr.info.open_orders.return_value = []

        result = mgr.cancel_all_orders()

        assert result == 0
        mgr.exchange.bulk_cancel.assert_not_called()
