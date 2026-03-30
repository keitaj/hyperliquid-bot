"""Tests for bulk order placement."""

from unittest.mock import MagicMock, patch
import pytest

from order_manager import OrderManager, Order, OrderSide, OrderStatus


@pytest.fixture(autouse=True)
def _bypass_api_wrapper():
    with patch('order_manager.api_wrapper') as mock_wrapper:
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        yield mock_wrapper


def _make_order_manager():
    exchange = MagicMock()
    info = MagicMock()
    return OrderManager(exchange, info, '0xabc')


def _make_order(coin='BTC', side=OrderSide.BUY, size=0.1, price=50000.0):
    return Order(
        id=None, coin=coin, side=side, size=size, price=price,
        order_type={"limit": {"tif": "Alo"}}, reduce_only=False,
    )


class TestBulkPlaceOrders:

    def test_bulk_success(self):
        """All orders placed successfully."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': [
                {'resting': {'oid': 100}},
                {'resting': {'oid': 200}},
            ]}},
        }

        orders = [_make_order(side=OrderSide.BUY), _make_order(side=OrderSide.SELL)]
        results = mgr.bulk_place_orders(orders)

        assert results[0] is not None
        assert results[0].id == 100
        assert results[1] is not None
        assert results[1].id == 200
        assert 100 in mgr.active_orders
        assert 200 in mgr.active_orders
        mgr.exchange.bulk_orders.assert_called_once()

    def test_bulk_partial_failure(self):
        """One order succeeds, one rejected."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': [
                {'resting': {'oid': 100}},
                {'error': 'insufficient margin'},
            ]}},
        }

        orders = [_make_order(), _make_order()]
        results = mgr.bulk_place_orders(orders)

        assert results[0] is not None
        assert results[0].id == 100
        assert results[1] is None
        assert orders[1].status == OrderStatus.REJECTED

    def test_bulk_empty(self):
        """Empty list returns empty without API call."""
        mgr = _make_order_manager()

        results = mgr.bulk_place_orders([])

        assert results == []
        mgr.exchange.bulk_orders.assert_not_called()

    def test_bulk_api_error(self):
        """API exception marks all orders as rejected."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.side_effect = Exception("network error")

        orders = [_make_order(), _make_order()]
        results = mgr.bulk_place_orders(orders)

        assert all(r is None for r in results)
        assert all(o.status == OrderStatus.REJECTED for o in orders)

    def test_bulk_bad_response(self):
        """Non-ok status marks all as rejected."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.return_value = {'status': 'error'}

        orders = [_make_order()]
        results = mgr.bulk_place_orders(orders)

        assert results[0] is None
        assert orders[0].status == OrderStatus.REJECTED

    def test_bulk_single_order(self):
        """Single order works via bulk API."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': [
                {'filled': {'oid': 300}},
            ]}},
        }

        orders = [_make_order()]
        results = mgr.bulk_place_orders(orders)

        assert results[0] is not None
        assert results[0].id == 300

    def test_bulk_builds_correct_request(self):
        """Order requests are built with correct fields."""
        mgr = _make_order_manager()
        mgr.exchange.bulk_orders.return_value = {
            'status': 'ok',
            'response': {'data': {'statuses': [
                {'resting': {'oid': 100}},
            ]}},
        }

        order = _make_order(
            coin='ETH', side=OrderSide.SELL, size=1.5, price=3000.0,
        )
        mgr.bulk_place_orders([order])

        call_args = mgr.exchange.bulk_orders.call_args[0][0]
        assert len(call_args) == 1
        req = call_args[0]
        assert req['coin'] == 'ETH'
        assert req['is_buy'] is False
        assert req['sz'] == 1.5
        assert req['limit_px'] == 3000.0
        assert req['reduce_only'] is False


class TestExtractOid:

    def test_direct_oid(self):
        assert OrderManager._extract_oid({'oid': 123}) == 123

    def test_resting_oid(self):
        assert OrderManager._extract_oid({'resting': {'oid': 456}}) == 456

    def test_filled_oid(self):
        assert OrderManager._extract_oid({'filled': {'oid': 789}}) == 789

    def test_error_returns_none(self):
        assert OrderManager._extract_oid({'error': 'nope'}) is None

    def test_empty_returns_none(self):
        assert OrderManager._extract_oid({}) is None
