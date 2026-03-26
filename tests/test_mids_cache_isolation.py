"""Verify that _mids_cache is per-instance, not shared across OrderManager instances."""

from unittest.mock import MagicMock

from order_manager import OrderManager


def _make_order_manager():
    om = OrderManager.__new__(OrderManager)
    om.exchange = MagicMock()
    om.info = MagicMock()
    om.account_address = "0xtest"
    om.default_slippage = 0.01
    om.active_orders = {}
    om._mids_cache = {}
    return om


def test_mids_cache_not_shared_between_instances():
    """Two OrderManager instances should have independent caches."""
    om1 = OrderManager(MagicMock(), MagicMock(), "0xaaa")
    om2 = OrderManager(MagicMock(), MagicMock(), "0xbbb")

    om1._mids_cache[""] = (999999999999, {"BTC": "50000"})

    assert om2._mids_cache.get("") is None
    assert "" not in om2._mids_cache
