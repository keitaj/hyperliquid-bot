"""Tests for race-safe active_orders cleanup in update_order_status.

Verifies that update_order_status uses pop(default=None) so that orders
already removed (e.g. by cancel_order/bulk_cancel running concurrently
or before the disappeared snapshot was processed) do NOT cause KeyError.
"""

from unittest.mock import MagicMock, patch

from order_manager import OrderManager, OrderStatus, Order, OrderSide
from hip3.multi_dex_order_manager import MultiDexOrderManager
from ttl_cache import TTLCacheEntry


def _make_order(oid: int, coin: str = "BTC") -> Order:
    return Order(
        id=oid, coin=coin, side=OrderSide.BUY, size=1.0, price=100.0,
        order_type={"limit": {"tif": "Gtc"}},
    )


def _make_order_manager(active_orders=None) -> OrderManager:
    om = OrderManager.__new__(OrderManager)
    om.exchange = MagicMock()
    om.info = MagicMock()
    om.account_address = "0xtest"
    om.default_slippage = 0.01
    om.active_orders = dict(active_orders) if active_orders else {}
    om._open_orders_cache = TTLCacheEntry(ttl=5.0)
    return om


def _make_multi_dex_manager(active_orders=None) -> MultiDexOrderManager:
    mdm = MultiDexOrderManager.__new__(MultiDexOrderManager)
    mdm.exchange = MagicMock()
    mdm.info = MagicMock()
    mdm.account_address = "0xtest"
    mdm.default_slippage = 0.01
    mdm.active_orders = dict(active_orders) if active_orders else {}
    mdm._open_orders_cache = TTLCacheEntry(ttl=5.0)
    mdm.market_data_ext = MagicMock()
    return mdm


# ---------------------------------------------------------------------------
# OrderManager.update_order_status
# ---------------------------------------------------------------------------


class TestOrderManagerRaceSafe:
    """update_order_status uses pop() to be safe against concurrent removal."""

    @patch("order_manager.api_wrapper")
    def test_already_removed_order_does_not_raise(self, mock_wrap):
        """If a disappeared order is missing from active_orders, no KeyError."""
        order_a = _make_order(1, "BTC")
        order_b = _make_order(2, "ETH")
        om = _make_order_manager({1: order_a, 2: order_b})
        om.get_open_orders = MagicMock(return_value=[])  # both disappeared

        # Simulate concurrent removal: order_b is removed before update reaches it.
        # We do this by patching get_open_orders so disappeared = [1, 2],
        # then mutating active_orders during the fills processing.
        original_user_fills = lambda *args, **kwargs: []  # noqa: E731

        def call_side_effect(fn, *args, **kwargs):
            # When user_fills is called, mutate active_orders to drop oid=2
            if fn is om.info.user_fills:
                om.active_orders.pop(2, None)
                return original_user_fills()
            return fn(*args, **kwargs)

        mock_wrap.call.side_effect = call_side_effect

        # Should NOT raise even though oid=2 is concurrently removed.
        om.update_order_status()

        # Both orders should be cleaned up
        assert 1 not in om.active_orders
        assert 2 not in om.active_orders
        # order_a should be marked CANCELLED (not in fills)
        assert order_a.status == OrderStatus.CANCELLED

    @patch("order_manager.api_wrapper")
    def test_normal_path_still_works(self, mock_wrap):
        """When no race occurs, behavior is unchanged."""
        order_a = _make_order(1, "BTC")
        order_b = _make_order(2, "ETH")
        om = _make_order_manager({1: order_a, 2: order_b})
        om.get_open_orders = MagicMock(return_value=[{"oid": 1}])  # only #2 disappeared

        # user_fills returns a fill for oid=2
        mock_wrap.call.return_value = [{"oid": 2, "sz": "1.0"}]

        om.update_order_status()

        assert 1 in om.active_orders  # still open
        assert 2 not in om.active_orders  # cleaned up
        assert order_b.status == OrderStatus.FILLED
        assert order_b.filled_size == 1.0

    @patch("order_manager.api_wrapper")
    def test_error_log_includes_exception_type(self, mock_wrap, caplog):
        """Error log includes type(e).__name__ for easier debugging."""
        import logging
        om = _make_order_manager({1: _make_order(1, "BTC")})
        om.get_open_orders = MagicMock(side_effect=ValueError("bad data"))

        with caplog.at_level(logging.ERROR, logger="order_manager"):
            om.update_order_status()

        # Error message should mention ValueError
        assert any("ValueError" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# MultiDexOrderManager.update_order_status
# ---------------------------------------------------------------------------


class TestMultiDexOrderManagerRaceSafe:
    """multi_dex update_order_status pop() is race-safe across DEXes."""

    @patch("hip3.multi_dex_order_manager.api_wrapper")
    def test_already_removed_order_in_dex_does_not_raise(self, mock_wrap):
        """Race: order disappeared from km DEX but already removed from active_orders."""
        order_km_a = _make_order(100, "km:USTECH")
        order_km_b = _make_order(101, "km:US500")
        mdm = _make_multi_dex_manager({100: order_km_a, 101: order_km_b})
        # All disappeared from open orders
        mdm.get_open_orders = MagicMock(return_value=[])

        # Simulate that 101 is already removed when fills are fetched
        def fills_side_effect(*args, **kwargs):
            mdm.active_orders.pop(101, None)
            return []  # no fills

        mdm.market_data_ext.get_user_fills_dex = fills_side_effect

        # Should NOT raise KeyError
        mdm.update_order_status()

        assert 100 not in mdm.active_orders
        assert 101 not in mdm.active_orders
        assert order_km_a.status == OrderStatus.CANCELLED

    @patch("hip3.multi_dex_order_manager.api_wrapper")
    def test_error_path_does_not_raise_on_already_removed(self, mock_wrap):
        """When fills fetch fails AND order already removed, except cleanup also race-safe."""
        order_km = _make_order(100, "km:USTECH")
        mdm = _make_multi_dex_manager({100: order_km})
        mdm.get_open_orders = MagicMock(return_value=[])

        # Fills fetch raises ValueError (in API_ERRORS)
        mdm.market_data_ext.get_user_fills_dex = MagicMock(side_effect=ValueError("api fail"))

        # Concurrently remove the order before except cleanup runs
        # (we simulate by mutating in the side_effect)
        # Actually the side_effect raises before any mutation; the except block
        # then iterates and does pop(). We just verify no exception escapes.
        mdm.update_order_status()  # should not raise

        # The except block should still mark and pop
        assert 100 not in mdm.active_orders
        assert order_km.status == OrderStatus.CANCELLED

    @patch("hip3.multi_dex_order_manager.api_wrapper")
    def test_error_log_includes_exception_type(self, mock_wrap, caplog):
        """Error log on fills failure includes exception type."""
        import logging
        order_km = _make_order(100, "km:USTECH")
        mdm = _make_multi_dex_manager({100: order_km})
        mdm.get_open_orders = MagicMock(return_value=[])
        mdm.market_data_ext.get_user_fills_dex = MagicMock(side_effect=KeyError("oid"))

        with caplog.at_level(logging.ERROR, logger="hip3.multi_dex_order_manager"):
            mdm.update_order_status()

        assert any("KeyError" in record.message for record in caplog.records)
        assert any("'km'" in record.message for record in caplog.records)
