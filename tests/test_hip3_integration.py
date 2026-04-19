"""Comprehensive HIP-3 multi-DEX integration tests.

Covers DEXRegistry, MultiDexMarketData, and MultiDexOrderManager
using mocks (no real API calls).
"""

from unittest.mock import MagicMock, patch
import pytest

from order_manager import Order, OrderSide
from ttl_cache import TTLCacheEntry, TTLCacheMap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_perp_dexs_response():
    """Simulated perpDexs API response (index 0 is null = standard HL)."""
    return [
        None,                        # 0 — standard Hyperliquid
        {"name": "xyz"},             # 1
        None,                        # 2 — gap
        {"name": "flx"},             # 3
    ]


def _make_meta_response(dex_name):
    """Simulated meta response for a given DEX."""
    if dex_name == "xyz":
        return {
            "universe": [
                {"name": "GOLD", "szDecimals": 2},
                {"name": "SILVER", "szDecimals": 1},
            ]
        }
    if dex_name == "flx":
        return {
            "universe": [
                {"name": "flx:NVDA", "szDecimals": 4},   # some APIs return prefixed
                {"name": "TSLA", "szDecimals": 3},
            ]
        }
    return {"universe": []}


def _make_order(oid, coin="BTC", side=OrderSide.BUY, size=1.0, price=100.0):
    return Order(
        id=oid,
        coin=coin,
        side=side,
        size=size,
        price=price,
        order_type={"limit": {"tif": "Gtc"}},
    )


def _make_multi_dex_om(active_orders=None, hip3_dexes=None):
    """Create a MultiDexOrderManager with mocked dependencies."""
    from hip3.multi_dex_order_manager import MultiDexOrderManager

    om = MultiDexOrderManager.__new__(MultiDexOrderManager)
    om.exchange = MagicMock()
    om.info = MagicMock()
    om.account_address = "0xtest"
    om.default_slippage = 0.01
    om.active_orders = dict(active_orders) if active_orders else {}
    om.hip3_dexes = hip3_dexes or ["xyz"]
    om.registry = MagicMock()
    om.market_data_ext = MagicMock()
    om._mids_cache = TTLCacheMap(ttl=5.0)
    om._user_state_cache = TTLCacheEntry(ttl=2.0)
    om._user_state_cache_ttl = 2.0
    om._open_orders_cache = TTLCacheEntry(ttl=2.0)
    return om


# ===========================================================================
# DEXRegistry tests
# ===========================================================================

class TestDEXRegistryDiscover:
    """Test DEXRegistry.discover() with mock API responses."""

    @patch("hip3.dex_registry.requests.post")
    def test_discover_parses_perp_dexs_and_meta(self, mock_post):
        """discover() fetches perpDexs, then meta for each DEX, computing asset IDs."""
        from hip3.dex_registry import DEXRegistry

        def mock_post_side_effect(url, json=None, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if json.get("type") == "perpDexs":
                resp.json.return_value = _make_perp_dexs_response()
            elif json.get("type") == "meta":
                resp.json.return_value = _make_meta_response(json["dex"])
            return resp

        mock_post.side_effect = mock_post_side_effect

        registry = DEXRegistry("https://api.example.com")
        registry.discover()

        # Should have registered xyz (index 1) and flx (index 3)
        assert set(registry.get_dex_names()) == {"xyz", "flx"}

        # xyz: perp_dex_index=1 → GOLD=100000+1*10000+0=110000, SILVER=110001
        assert registry.get_asset_id("xyz", "GOLD") == 110000
        assert registry.get_asset_id("xyz", "SILVER") == 110001

        # flx: perp_dex_index=3 → NVDA=100000+3*10000+0=130000, TSLA=130001
        # Note: "flx:NVDA" in meta is parsed to bare "NVDA"
        assert registry.get_asset_id("flx", "NVDA") == 130000
        assert registry.get_asset_id("flx", "TSLA") == 130001

    @patch("hip3.dex_registry.requests.post")
    def test_discover_with_target_dexes_filtering(self, mock_post):
        """discover(target_dexes=["xyz"]) only registers requested DEXes."""
        from hip3.dex_registry import DEXRegistry

        def mock_post_side_effect(url, json=None, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if json.get("type") == "perpDexs":
                resp.json.return_value = _make_perp_dexs_response()
            elif json.get("type") == "meta":
                resp.json.return_value = _make_meta_response(json["dex"])
            return resp

        mock_post.side_effect = mock_post_side_effect

        registry = DEXRegistry("https://api.example.com")
        registry.discover(target_dexes=["xyz"])

        assert registry.get_dex_names() == ["xyz"]
        assert registry.get_asset_id("xyz", "GOLD") == 110000
        # flx should NOT be registered
        assert registry.get_asset_id("flx", "NVDA") is None

    @patch("hip3.dex_registry.requests.post")
    def test_discover_handles_null_entries(self, mock_post):
        """Null entries in perpDexs are silently skipped."""
        from hip3.dex_registry import DEXRegistry

        resp = MagicMock()
        resp.raise_for_status = MagicMock()

        def mock_post_side_effect(url, json=None, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if json.get("type") == "perpDexs":
                r.json.return_value = [None, None, None]
            return r

        mock_post.side_effect = mock_post_side_effect

        registry = DEXRegistry("https://api.example.com")
        registry.discover()

        assert registry.get_dex_names() == []

    @patch("hip3.dex_registry.requests.post")
    def test_discover_api_failure_raises_runtime_error(self, mock_post):
        """API failure on perpDexs raises RuntimeError."""
        from hip3.dex_registry import DEXRegistry
        import requests

        mock_post.side_effect = requests.exceptions.ConnectionError("timeout")

        registry = DEXRegistry("https://api.example.com")
        with pytest.raises(RuntimeError, match="Failed to fetch perpDexs"):
            registry.discover()

    @patch("hip3.dex_registry.requests.post")
    def test_discover_warns_on_missing_target_dexes(self, mock_post, caplog):
        """discover(target_dexes=["missing"]) logs a warning."""
        from hip3.dex_registry import DEXRegistry
        import logging

        def mock_post_side_effect(url, json=None, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if json.get("type") == "perpDexs":
                r.json.return_value = _make_perp_dexs_response()
            elif json.get("type") == "meta":
                r.json.return_value = _make_meta_response(json["dex"])
            return r

        mock_post.side_effect = mock_post_side_effect

        registry = DEXRegistry("https://api.example.com")
        with caplog.at_level(logging.WARNING, logger="hip3.dex_registry"):
            registry.discover(target_dexes=["xyz", "missing_dex"])

        assert "missing_dex" in caplog.text

    @patch("hip3.dex_registry.requests.post")
    def test_discover_meta_failure_logs_error_and_continues(self, mock_post, caplog):
        """If meta fetch fails for one DEX, other DEXes still register."""
        from hip3.dex_registry import DEXRegistry
        import requests
        import logging

        def mock_post_side_effect(url, json=None, **kwargs):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if json.get("type") == "perpDexs":
                r.json.return_value = _make_perp_dexs_response()
            elif json.get("type") == "meta":
                if json["dex"] == "xyz":
                    raise requests.exceptions.ConnectionError("meta fail")
                r.json.return_value = _make_meta_response(json["dex"])
            return r

        mock_post.side_effect = mock_post_side_effect

        registry = DEXRegistry("https://api.example.com")
        with caplog.at_level(logging.ERROR, logger="hip3.dex_registry"):
            registry.discover()

        # xyz failed, but flx should still be registered
        assert "xyz" not in registry.get_dex_names()
        assert "flx" in registry.get_dex_names()
        assert "Failed to load meta for DEX 'xyz'" in caplog.text


class TestDEXRegistryLookups:
    """Test DEXRegistry lookup helpers with pre-populated data."""

    def _make_registry(self):
        from hip3.dex_registry import DEXRegistry

        registry = DEXRegistry("https://api.example.com")
        registry._dexes = {
            "xyz": {
                "perp_dex_index": 1,
                "assets": {
                    "GOLD": {"asset_id": 110000, "sz_decimals": 2, "meta_idx": 0},
                    "SILVER": {"asset_id": 110001, "sz_decimals": 1, "meta_idx": 1},
                },
                "meta": {"universe": [{"name": "GOLD"}, {"name": "SILVER"}]},
            },
            "flx": {
                "perp_dex_index": 3,
                "assets": {
                    "NVDA": {"asset_id": 130000, "sz_decimals": 4, "meta_idx": 0},
                },
                "meta": {"universe": [{"name": "NVDA"}]},
            },
        }
        return registry

    def test_get_asset_id(self):
        reg = self._make_registry()
        assert reg.get_asset_id("xyz", "GOLD") == 110000
        assert reg.get_asset_id("xyz", "SILVER") == 110001
        assert reg.get_asset_id("flx", "NVDA") == 130000
        assert reg.get_asset_id("xyz", "NONEXISTENT") is None
        assert reg.get_asset_id("nonexistent_dex", "GOLD") is None

    def test_get_sz_decimals(self):
        reg = self._make_registry()
        assert reg.get_sz_decimals("xyz", "GOLD") == 2
        assert reg.get_sz_decimals("xyz", "SILVER") == 1
        assert reg.get_sz_decimals("flx", "NVDA") == 4
        # Fallback default for unknown coin
        assert reg.get_sz_decimals("xyz", "UNKNOWN") == 3

    def test_get_meta(self):
        reg = self._make_registry()
        meta = reg.get_meta("xyz")
        assert "universe" in meta
        assert len(meta["universe"]) == 2
        # Missing DEX returns empty dict
        assert reg.get_meta("nonexistent") == {}

    def test_get_dex_names(self):
        reg = self._make_registry()
        assert set(reg.get_dex_names()) == {"xyz", "flx"}

    def test_list_coins(self):
        reg = self._make_registry()
        assert set(reg.list_coins("xyz")) == {"GOLD", "SILVER"}
        assert reg.list_coins("flx") == ["NVDA"]
        assert reg.list_coins("nonexistent") == []

    def test_build_coin_to_asset_map(self):
        reg = self._make_registry()
        asset_map = reg.build_coin_to_asset_map()
        assert asset_map == {
            "xyz:GOLD": 110000,
            "xyz:SILVER": 110001,
            "flx:NVDA": 130000,
        }


# ===========================================================================
# MultiDexMarketData tests
# ===========================================================================

class TestMultiDexMarketData:

    def _make_mdm(self):
        from hip3.multi_dex_market_data import MultiDexMarketData

        mdm = MultiDexMarketData.__new__(MultiDexMarketData)
        mdm.info = MagicMock()
        mdm.registry = MagicMock()
        mdm.api_url = "https://api.example.com"
        mdm._cache = TTLCacheMap(ttl=2.0)
        mdm._cache_ttl = 2.0
        mdm._meta_cache = TTLCacheEntry(ttl=3600)
        mdm._meta_cache_ttl = 3600
        mdm._dex_user_state_cache = TTLCacheMap(ttl=2.0)
        mdm._user_state_cache_ttl = 2.0
        mdm._dex_open_orders_cache = TTLCacheMap(ttl=2.0)
        return mdm

    def test_get_sz_decimals_hip3_coin_via_sdk(self):
        """HIP-3 coin: tries SDK coin_to_asset first."""
        mdm = self._make_mdm()
        mdm.info.coin_to_asset = {"xyz:GOLD": 110000}
        mdm.info.asset_to_sz_decimals = {110000: 2}

        assert mdm.get_sz_decimals("xyz:GOLD") == 2

    def test_get_sz_decimals_hip3_coin_fallback_to_registry(self):
        """HIP-3 coin: falls back to registry if SDK doesn't have it."""
        mdm = self._make_mdm()
        mdm.info.coin_to_asset = {}  # SDK doesn't know about it
        mdm.registry.get_sz_decimals.return_value = 4

        assert mdm.get_sz_decimals("flx:NVDA") == 4
        mdm.registry.get_sz_decimals.assert_called_once_with("flx", "NVDA")

    @patch("market_data.api_wrapper")
    def test_get_sz_decimals_standard_coin_delegates_to_base(self, mock_wrapper):
        """Standard coin delegates to base MarketDataManager."""
        mdm = self._make_mdm()
        mock_wrapper.call.return_value = {
            "universe": [{"name": "BTC", "szDecimals": 5}]
        }

        result = mdm.get_sz_decimals("BTC")
        assert result == 5

    def test_get_all_mids_with_dex_parameter(self):
        """get_all_mids(dex=...) passes dex to info.all_mids."""
        mdm = self._make_mdm()
        mdm.info.all_mids.return_value = {"xyz:GOLD": "1800.50", "xyz:SILVER": "25.10"}

        result = mdm.get_all_mids(dex="xyz")

        mdm.info.all_mids.assert_called_once_with(dex="xyz")
        assert result == {"xyz:GOLD": 1800.50, "xyz:SILVER": 25.10}

    def test_get_all_mids_no_dex(self):
        """get_all_mids() without dex passes empty string."""
        mdm = self._make_mdm()
        mdm.info.all_mids.return_value = {"BTC": "50000"}

        result = mdm.get_all_mids()

        mdm.info.all_mids.assert_called_once_with(dex="")
        assert result == {"BTC": 50000.0}

    def test_get_all_mids_error_returns_empty(self):
        """get_all_mids returns empty dict on error."""
        import requests
        mdm = self._make_mdm()
        mdm.info.all_mids.side_effect = requests.exceptions.ConnectionError("fail")

        assert mdm.get_all_mids(dex="xyz") == {}

    def test_get_user_state_passes_dex(self):
        """get_user_state passes dex parameter correctly."""
        mdm = self._make_mdm()
        expected = {"assetPositions": []}
        mdm.info.user_state.return_value = expected

        result = mdm.get_user_state("0xabc", dex="xyz")

        mdm.info.user_state.assert_called_once_with("0xabc", dex="xyz")
        assert result == expected

    def test_get_user_state_error_returns_empty(self):
        """get_user_state returns empty dict on error."""
        import requests
        mdm = self._make_mdm()
        mdm.info.user_state.side_effect = requests.exceptions.ConnectionError("fail")

        assert mdm.get_user_state("0xabc", dex="xyz") == {}

    def test_get_open_orders_dex_passes_dex(self):
        """get_open_orders_dex passes dex parameter correctly."""
        mdm = self._make_mdm()
        expected_orders = [{"oid": 1, "coin": "GOLD"}]
        mdm.info.open_orders.return_value = expected_orders

        result = mdm.get_open_orders_dex("0xabc", dex="xyz")

        mdm.info.open_orders.assert_called_once_with("0xabc", dex="xyz")
        assert result == expected_orders

    def test_get_open_orders_dex_error_returns_empty_list(self):
        """get_open_orders_dex returns empty list on error."""
        import requests
        mdm = self._make_mdm()
        mdm.info.open_orders.side_effect = requests.exceptions.ConnectionError("fail")

        assert mdm.get_open_orders_dex("0xabc", dex="xyz") == []

    def test_get_user_state_caches_per_dex(self):
        """Repeated get_user_state calls for the same DEX use cache."""
        mdm = self._make_mdm()
        expected = {"assetPositions": [{"position": {"coin": "GOLD"}}]}
        mdm.info.user_state.return_value = expected

        result1 = mdm.get_user_state("0xabc", dex="xyz")
        result2 = mdm.get_user_state("0xabc", dex="xyz")

        assert result1 == expected
        assert result2 == expected
        assert mdm.info.user_state.call_count == 1

    def test_get_user_state_cache_separates_dexes(self):
        """Different DEXes get separate cache entries."""
        mdm = self._make_mdm()
        mdm.info.user_state.side_effect = [
            {"assetPositions": [{"position": {"coin": "GOLD"}}]},
            {"assetPositions": [{"position": {"coin": "NVDA"}}]},
        ]

        result_xyz = mdm.get_user_state("0xabc", dex="xyz")
        result_flx = mdm.get_user_state("0xabc", dex="flx")

        assert result_xyz["assetPositions"][0]["position"]["coin"] == "GOLD"
        assert result_flx["assetPositions"][0]["position"]["coin"] == "NVDA"
        assert mdm.info.user_state.call_count == 2

    def test_get_user_state_cache_expires(self):
        """After TTL expires, the cache is refreshed."""
        mdm = self._make_mdm()
        mdm._dex_user_state_cache = TTLCacheMap(ttl=0.0)  # Expire immediately
        mdm.info.user_state.return_value = {"assetPositions": []}

        mdm.get_user_state("0xabc", dex="xyz")
        mdm.get_user_state("0xabc", dex="xyz")

        assert mdm.info.user_state.call_count == 2

    def test_get_user_state_cache_error_not_cached(self):
        """API errors are not cached — next call retries."""
        import requests
        mdm = self._make_mdm()
        expected = {"assetPositions": []}
        mdm.info.user_state.side_effect = [
            requests.exceptions.ConnectionError("fail"),
            expected,
        ]

        result1 = mdm.get_user_state("0xabc", dex="xyz")
        result2 = mdm.get_user_state("0xabc", dex="xyz")

        assert result1 == {}  # Error fallback
        assert result2 == expected  # Retry succeeded
        assert mdm.info.user_state.call_count == 2


# ===========================================================================
# MultiDexOrderManager tests
# ===========================================================================

class TestMultiDexGetPosition:

    def test_get_position_hip3_coin_fetches_from_correct_dex(self):
        """get_position("xyz:GOLD") queries the xyz DEX and matches coin name."""
        om = _make_multi_dex_om()
        om.market_data_ext.get_user_state.return_value = {
            "assetPositions": [
                {"position": {"coin": "GOLD", "szi": "10.0", "entryPx": "1800"}},
                {"position": {"coin": "SILVER", "szi": "5.0", "entryPx": "25"}},
            ]
        }

        pos = om.get_position("xyz:GOLD")

        om.market_data_ext.get_user_state.assert_called_once_with("0xtest", dex="xyz")
        assert pos["coin"] == "GOLD"
        assert pos["szi"] == "10.0"

    def test_get_position_hip3_coin_not_found(self):
        """get_position("xyz:PLATINUM") returns None when no match."""
        om = _make_multi_dex_om()
        om.market_data_ext.get_user_state.return_value = {
            "assetPositions": [
                {"position": {"coin": "GOLD", "szi": "10.0", "entryPx": "1800"}},
            ]
        }

        assert om.get_position("xyz:PLATINUM") is None

    @patch("order_manager.api_wrapper")
    def test_get_position_standard_coin_delegates_to_base(self, mock_wrapper):
        """get_position("BTC") delegates to base OrderManager."""
        om = _make_multi_dex_om()
        mock_wrapper.call.return_value = {
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "0.5", "entryPx": "50000"}},
            ]
        }

        pos = om.get_position("BTC")

        assert pos["coin"] == "BTC"
        # Should NOT call market_data_ext
        om.market_data_ext.get_user_state.assert_not_called()


class TestMultiDexGetAllPositions:

    @patch("order_manager.api_wrapper")
    def test_aggregates_standard_and_hip3_positions(self, mock_wrapper):
        """get_all_positions merges standard HL + HIP-3 positions with prefixing."""
        om = _make_multi_dex_om(hip3_dexes=["xyz", "flx"])

        # Standard HL positions (via base class _get_cached_user_state)
        mock_wrapper.call.return_value = {
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "0.5"}},
            ]
        }

        # HIP-3 positions
        def mock_user_state(address, dex=""):
            if dex == "xyz":
                return {"assetPositions": [
                    {"position": {"coin": "GOLD", "szi": "10"}},
                ]}
            if dex == "flx":
                return {"assetPositions": [
                    {"position": {"coin": "NVDA", "szi": "3"}},
                ]}
            return {}

        om.market_data_ext.get_user_state.side_effect = mock_user_state

        positions = om.get_all_positions()

        coins = [p["coin"] for p in positions]
        assert "BTC" in coins
        assert "xyz:GOLD" in coins
        assert "flx:NVDA" in coins
        assert len(positions) == 3

    @patch("order_manager.api_wrapper")
    def test_error_in_one_dex_doesnt_block_others(self, mock_wrapper):
        """Error in one DEX still returns positions from other DEXes."""
        import requests
        om = _make_multi_dex_om(hip3_dexes=["xyz", "flx"])

        mock_wrapper.call.return_value = {
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "0.5"}},
            ]
        }

        def mock_user_state(address, dex=""):
            if dex == "xyz":
                raise requests.exceptions.ConnectionError("API down")
            if dex == "flx":
                return {"assetPositions": [
                    {"position": {"coin": "NVDA", "szi": "3"}},
                ]}
            return {}

        om.market_data_ext.get_user_state.side_effect = mock_user_state

        positions = om.get_all_positions()

        coins = [p["coin"] for p in positions]
        assert "BTC" in coins
        assert "flx:NVDA" in coins
        # xyz failed, so no xyz positions
        assert not any("xyz:" in c for c in coins)


class TestMultiDexGetOpenOrders:

    @patch("hip3.multi_dex_order_manager.api_wrapper")
    def test_aggregates_across_dexes(self, mock_wrapper):
        """get_open_orders() returns orders from standard HL + all HIP-3 DEXes."""
        om = _make_multi_dex_om(hip3_dexes=["xyz"])

        # Standard HL orders
        mock_wrapper.call.return_value = [
            {"oid": 1, "coin": "BTC"},
        ]
        # HIP-3 orders
        om.market_data_ext.get_open_orders_dex.return_value = [
            {"oid": 100, "coin": "GOLD"},
        ]

        orders = om.get_open_orders()

        assert len(orders) == 2
        coins = [o["coin"] for o in orders]
        assert "BTC" in coins
        assert "xyz:GOLD" in coins

    @patch("hip3.multi_dex_order_manager.api_wrapper")
    def test_filter_by_standard_coin(self, mock_wrapper):
        """get_open_orders("BTC") filters standard HL orders correctly."""
        om = _make_multi_dex_om(hip3_dexes=["xyz"])

        mock_wrapper.call.return_value = [
            {"oid": 1, "coin": "BTC"},
            {"oid": 2, "coin": "ETH"},
        ]
        om.market_data_ext.get_open_orders_dex.return_value = [
            {"oid": 100, "coin": "GOLD"},
        ]

        orders = om.get_open_orders("BTC")

        btc_orders = [o for o in orders if o["coin"] == "BTC"]
        assert len(btc_orders) == 1
        assert btc_orders[0]["oid"] == 1

    @patch("hip3.multi_dex_order_manager.api_wrapper")
    def test_filter_by_hip3_coin_only_queries_that_dex(self, mock_wrapper):
        """get_open_orders("xyz:GOLD") only queries the xyz DEX, not standard HL."""
        om = _make_multi_dex_om(hip3_dexes=["xyz", "flx"])

        om.market_data_ext.get_open_orders_dex.return_value = [
            {"oid": 100, "coin": "GOLD"},
            {"oid": 101, "coin": "SILVER"},
        ]

        orders = om.get_open_orders("xyz:GOLD")

        # Should NOT call standard HL open_orders
        mock_wrapper.call.assert_not_called()
        # Should only query xyz DEX
        om.market_data_ext.get_open_orders_dex.assert_called_once_with("0xtest", dex="xyz")
        # Should filter to just GOLD
        assert len(orders) == 1
        assert orders[0]["coin"] == "xyz:GOLD"


class TestMultiDexCancelAllOrders:

    @patch("order_manager.api_wrapper")
    def test_cancel_all_across_dexes(self, mock_wrapper):
        """cancel_all_orders() cancels across standard HL + all HIP-3 DEXes."""
        om = _make_multi_dex_om(hip3_dexes=["xyz"])

        # HIP-3 open orders
        om.market_data_ext.get_open_orders_dex.return_value = [
            {"oid": 100, "coin": "GOLD"},
        ]

        # api_wrapper.call is used for:
        # 1. info.open_orders (super cancel_all_orders)
        # 2. exchange.bulk_cancel (super cancel_all_orders)
        # 3. exchange.bulk_cancel (hip3 cancel)
        mock_wrapper.call.side_effect = [
            [{"oid": 1, "coin": "BTC"}],    # open_orders for super().cancel_all_orders
            {"status": "ok", "response": {"data": {"statuses": ["success"]}}},  # bulk_cancel for HL
            {"status": "ok", "response": {"data": {"statuses": ["success"]}}},  # bulk_cancel for HIP-3
        ]

        result = om.cancel_all_orders()

        assert result == 2  # 1 from HL + 1 from HIP-3

    @patch("order_manager.api_wrapper")
    def test_cancel_all_hip3_coin_only_cancels_on_that_dex(self, mock_wrapper):
        """cancel_all_orders("xyz:GOLD") only cancels on xyz DEX."""
        om = _make_multi_dex_om(hip3_dexes=["xyz", "flx"])

        # HIP-3 open orders for xyz
        om.market_data_ext.get_open_orders_dex.return_value = [
            {"oid": 100, "coin": "GOLD"},
            {"oid": 101, "coin": "SILVER"},
        ]

        # bulk_cancel for HIP-3 (only GOLD should be cancelled)
        mock_wrapper.call.return_value = {
            "status": "ok",
            "response": {"data": {"statuses": ["success"]}},
        }

        om.cancel_all_orders("xyz:GOLD")

        # Should only query xyz DEX
        om.market_data_ext.get_open_orders_dex.assert_called_once_with("0xtest", dex="xyz")
        # Verify the cancel request was for GOLD only
        cancel_call = mock_wrapper.call.call_args
        cancel_requests = cancel_call[0][1]  # second positional arg to bulk_cancel
        assert len(cancel_requests) == 1
        assert cancel_requests[0]["coin"] == "xyz:GOLD"
        assert cancel_requests[0]["oid"] == 100

    @patch("order_manager.api_wrapper")
    def test_error_in_one_dex_doesnt_prevent_others(self, mock_wrapper):
        """Error in one DEX still processes other DEXes."""
        import requests
        om = _make_multi_dex_om(hip3_dexes=["xyz", "flx"])

        # Standard HL has no orders, then bulk_cancel for flx
        mock_wrapper.call.side_effect = [
            [],   # open_orders for super().cancel_all_orders — no HL orders
            {"status": "ok", "response": {"data": {"statuses": ["success"]}}},  # bulk_cancel for flx
        ]

        def mock_open_orders_dex(address, dex=""):
            if dex == "xyz":
                raise requests.exceptions.ConnectionError("xyz down")
            if dex == "flx":
                return [{"oid": 200, "coin": "NVDA"}]
            return []

        om.market_data_ext.get_open_orders_dex.side_effect = mock_open_orders_dex

        result = om.cancel_all_orders()

        # flx should still have been cancelled (1 order)
        assert result >= 1
