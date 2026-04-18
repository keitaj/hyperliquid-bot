"""Verify that _mids_cache is per-instance, not shared across OrderManager instances."""

from unittest.mock import MagicMock

from order_manager import OrderManager


def test_mids_cache_not_shared_between_instances():
    """Two OrderManager instances should have independent caches."""
    om1 = OrderManager(MagicMock(), MagicMock(), "0xaaa")
    om2 = OrderManager(MagicMock(), MagicMock(), "0xbbb")

    om1._mids_cache.set("", {"BTC": "50000"})

    assert om2._mids_cache.get("") is None
