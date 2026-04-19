"""Unit tests for OrderManager.update_order_status and MultiDexOrderManager.update_order_status.

Verifies that user_fills is fetched once (not per-order) and that fill sizes
are correctly aggregated across partial fills.
"""

from unittest.mock import MagicMock, patch, call
import pytest

from order_manager import OrderManager, OrderStatus, Order, OrderSide
from ttl_cache import TTLCacheEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order_manager(active_orders=None):
    """Create an OrderManager with mocked exchange/info."""
    om = OrderManager.__new__(OrderManager)
    om.exchange = MagicMock()
    om.info = MagicMock()
    om.account_address = "0xtest"
    om.default_slippage = 0.01
    om.active_orders = dict(active_orders) if active_orders else {}
    om._open_orders_cache = TTLCacheEntry(ttl=5.0)
    return om


def _make_order(oid, coin="BTC", side=OrderSide.BUY, size=1.0, price=100.0):
    return Order(
        id=oid,
        coin=coin,
        side=side,
        size=size,
        price=price,
        order_type={"limit": {"tif": "Gtc"}},
    )


# ---------------------------------------------------------------------------
# OrderManager.update_order_status
# ---------------------------------------------------------------------------

class TestUpdateOrderStatus:

    @patch("order_manager.api_wrapper")
    def test_no_active_orders_skips_fills_call(self, mock_wrapper):
        """When there are no active orders, user_fills should not be called."""
        om = _make_order_manager()
        mock_wrapper.call.return_value = []  # open_orders returns empty

        om.update_order_status()

        # Only open_orders call, no user_fills
        assert mock_wrapper.call.call_count == 1
        mock_wrapper.call.assert_called_once_with(om.info.open_orders, "0xtest")

    @patch("order_manager.api_wrapper")
    def test_all_orders_still_open_skips_fills_call(self, mock_wrapper):
        """When all active orders are still on the book, user_fills should not be called."""
        order = _make_order(101)
        om = _make_order_manager({101: order})

        mock_wrapper.call.return_value = [{"oid": 101, "coin": "BTC"}]

        om.update_order_status()

        # Only open_orders, no user_fills
        assert mock_wrapper.call.call_count == 1
        assert 101 in om.active_orders

    @patch("order_manager.api_wrapper")
    def test_fills_fetched_once_for_multiple_disappeared(self, mock_wrapper):
        """user_fills should be called exactly once even when multiple orders disappear."""
        orders = {
            101: _make_order(101, coin="BTC"),
            102: _make_order(102, coin="ETH"),
            103: _make_order(103, coin="SOL"),
        }
        om = _make_order_manager(orders)

        # None of the 3 orders are on the book anymore
        mock_wrapper.call.side_effect = [
            [],  # open_orders returns empty
            [    # user_fills returns fills for 101 and 103
                {"oid": 101, "sz": "1.0"},
                {"oid": 103, "sz": "0.5"},
            ],
        ]

        om.update_order_status()

        # 2 calls total: open_orders + user_fills
        assert mock_wrapper.call.call_count == 2
        assert mock_wrapper.call.call_args_list[1] == call(om.info.user_fills, "0xtest")

    @patch("order_manager.api_wrapper")
    def test_filled_order_gets_correct_status_and_size(self, mock_wrapper):
        """A disappeared order with a matching fill should be marked FILLED."""
        order = _make_order(101)
        om = _make_order_manager({101: order})

        mock_wrapper.call.side_effect = [
            [],
            [{"oid": 101, "sz": "2.5"}],
        ]

        om.update_order_status()

        assert order.status == OrderStatus.FILLED
        assert order.filled_size == 2.5
        assert 101 not in om.active_orders

    @patch("order_manager.api_wrapper")
    def test_cancelled_order_when_no_fill(self, mock_wrapper):
        """A disappeared order with no matching fill should be marked CANCELLED."""
        order = _make_order(101)
        om = _make_order_manager({101: order})

        mock_wrapper.call.side_effect = [
            [],
            [{"oid": 999, "sz": "1.0"}],  # fill for a different order
        ]

        om.update_order_status()

        assert order.status == OrderStatus.CANCELLED
        assert 101 not in om.active_orders

    @patch("order_manager.api_wrapper")
    def test_partial_fills_aggregated(self, mock_wrapper):
        """Multiple fills for the same oid should have their sizes summed."""
        order = _make_order(101)
        om = _make_order_manager({101: order})

        mock_wrapper.call.side_effect = [
            [],
            [
                {"oid": 101, "sz": "0.3"},
                {"oid": 101, "sz": "0.7"},
                {"oid": 101, "sz": "0.5"},
            ],
        ]

        om.update_order_status()

        assert order.status == OrderStatus.FILLED
        assert order.filled_size == pytest.approx(1.5)

    @patch("order_manager.api_wrapper")
    def test_mixed_filled_and_cancelled(self, mock_wrapper):
        """Some disappeared orders are filled, others are cancelled."""
        filled_order = _make_order(101, coin="BTC")
        cancelled_order = _make_order(102, coin="ETH")
        om = _make_order_manager({101: filled_order, 102: cancelled_order})

        mock_wrapper.call.side_effect = [
            [],
            [{"oid": 101, "sz": "1.0"}],  # only 101 has a fill
        ]

        om.update_order_status()

        assert filled_order.status == OrderStatus.FILLED
        assert filled_order.filled_size == 1.0
        assert cancelled_order.status == OrderStatus.CANCELLED
        assert 101 not in om.active_orders
        assert 102 not in om.active_orders

    @patch("order_manager.api_wrapper")
    def test_orders_still_on_book_not_touched(self, mock_wrapper):
        """Orders that are still on the book should remain active and untouched."""
        on_book = _make_order(101)
        disappeared = _make_order(102)
        om = _make_order_manager({101: on_book, 102: disappeared})

        mock_wrapper.call.side_effect = [
            [{"oid": 101, "coin": "BTC"}],  # 101 still open
            [{"oid": 102, "sz": "1.0"}],     # 102 was filled
        ]

        om.update_order_status()

        assert 101 in om.active_orders
        assert on_book.status == OrderStatus.PENDING
        assert 102 not in om.active_orders
        assert disappeared.status == OrderStatus.FILLED


# ---------------------------------------------------------------------------
# MultiDexOrderManager.update_order_status
# ---------------------------------------------------------------------------

class TestMultiDexUpdateOrderStatus:

    def _make_multi_dex_om(self, active_orders=None):
        """Create a MultiDexOrderManager with mocked dependencies."""
        from hip3.multi_dex_order_manager import MultiDexOrderManager

        om = MultiDexOrderManager.__new__(MultiDexOrderManager)
        om.exchange = MagicMock()
        om.info = MagicMock()
        om.account_address = "0xtest"
        om.default_slippage = 0.01
        om.active_orders = dict(active_orders) if active_orders else {}
        om.hip3_dexes = ["xyz"]
        om.registry = MagicMock()
        om.market_data_ext = MagicMock()
        return om

    @patch("hip3.multi_dex_order_manager.api_wrapper")
    def test_groups_by_dex_and_fetches_once_per_dex(self, mock_wrapper):
        """Fills should be fetched once per DEX, not per order."""
        hl_order = _make_order(101, coin="BTC")
        hip3_order_1 = _make_order(201, coin="xyz:GOLD")
        hip3_order_2 = _make_order(202, coin="xyz:SILVER")

        om = self._make_multi_dex_om({101: hl_order, 201: hip3_order_1, 202: hip3_order_2})

        # open_orders returns empty (all disappeared)
        mock_wrapper.call.side_effect = [
            [],  # get_open_orders -> open_orders for HL
            [{"oid": 101, "sz": "1.0"}],  # user_fills for standard HL
        ]
        # HIP-3 open orders (called by get_open_orders)
        om.market_data_ext.get_open_orders_dex.return_value = []
        # HIP-3 fills
        om.market_data_ext.get_user_fills_dex.return_value = [
            {"oid": 201, "sz": "5.0"},
            {"oid": 202, "sz": "3.0"},
        ]

        om.update_order_status()

        # user_fills for standard HL called once
        assert mock_wrapper.call.call_args_list[-1] == call(om.info.user_fills, "0xtest")
        # get_user_fills_dex called once for "xyz" (not twice)
        om.market_data_ext.get_user_fills_dex.assert_called_once_with("0xtest", "xyz")

        assert hl_order.status == OrderStatus.FILLED
        assert hip3_order_1.status == OrderStatus.FILLED
        assert hip3_order_1.filled_size == 5.0
        assert hip3_order_2.status == OrderStatus.FILLED
        assert hip3_order_2.filled_size == 3.0

    @patch("hip3.multi_dex_order_manager.api_wrapper")
    def test_dex_fills_error_marks_cancelled(self, mock_wrapper):
        """If fetching fills for a DEX fails, orders should be marked CANCELLED."""
        hip3_order = _make_order(201, coin="xyz:GOLD")
        om = self._make_multi_dex_om({201: hip3_order})

        mock_wrapper.call.return_value = []  # open_orders
        om.market_data_ext.get_open_orders_dex.return_value = []
        om.market_data_ext.get_user_fills_dex.side_effect = ConnectionError("API error")

        om.update_order_status()

        assert hip3_order.status == OrderStatus.CANCELLED
        assert 201 not in om.active_orders

    @patch("hip3.multi_dex_order_manager.api_wrapper")
    def test_no_disappeared_orders_skips_fills(self, mock_wrapper):
        """When all orders are still open, no fills should be fetched."""
        order = _make_order(101, coin="BTC")
        om = self._make_multi_dex_om({101: order})

        mock_wrapper.call.return_value = [{"oid": 101, "coin": "BTC"}]
        om.market_data_ext.get_open_orders_dex.return_value = []

        om.update_order_status()

        om.market_data_ext.get_user_fills_dex.assert_not_called()
        assert 101 in om.active_orders
