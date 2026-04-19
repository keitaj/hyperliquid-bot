"""Tests for round_price with sz_decimals and is_perp support."""

from unittest.mock import MagicMock, patch

from order_manager import round_price, OrderManager


class TestRoundPriceDefaults:
    """Default behaviour (sz_decimals=0, is_perp=True) matches legacy."""

    def test_five_sig_figs(self):
        assert round_price(12345.6789) == 12346.0

    def test_small_price(self):
        assert round_price(0.00123456) == 0.001235

    def test_six_decimal_places_cap(self):
        # 5 sig figs: 0.0012345 -> "0.0012345", then round to 6 dp -> 0.001234
        assert round_price(0.0012345) == 0.001234


class TestSzDecimalsPerp:
    """Perp prices use max_decimals=6, so price decimals = 6 - sz_decimals."""

    def test_sz_decimals_0_many_dp(self):
        # f"{0.00123456:.5g}" = "0.0012346" -> round(..., 6) = 0.001235
        assert round_price(0.00123456, sz_decimals=0) == 0.001235

    def test_sz_decimals_2_limits_dp(self):
        # f"{0.00123456:.5g}" = "0.0012346" (7 dp) -> round(..., 4) = 0.0012
        assert round_price(0.00123456, sz_decimals=2) == 0.0012

    def test_sz_decimals_3(self):
        # 6 - 3 = 3 decimal places
        assert round_price(50.123456, sz_decimals=3) == 50.123

    def test_sz_decimals_5(self):
        # 6 - 5 = 1 decimal place
        assert round_price(50.123456, sz_decimals=5) == 50.1


class TestSzDecimalsSpot:
    """Spot prices use max_decimals=8, so price decimals = 8 - sz_decimals."""

    def test_spot_sz_decimals_0(self):
        # 8 - 0 = 8 decimal places
        assert round_price(0.00123456, sz_decimals=0, is_perp=False) == 0.0012346

    def test_spot_sz_decimals_2(self):
        # 8 - 2 = 6 decimal places
        assert round_price(0.00123456, sz_decimals=2, is_perp=False) == 0.001235

    def test_spot_sz_decimals_4(self):
        # 8 - 4 = 4 decimal places; f"{0.00123456:.5g}" = "0.0012346"
        # round("0.0012346", 4) = 0.0012
        assert round_price(0.00123456, sz_decimals=4, is_perp=False) == 0.0012


class TestHighPriceIntegerRule:
    """Prices above 100,000 are always rounded to integers."""

    def test_above_100k_rounds_to_int(self):
        assert round_price(100_001.99) == 100002.0

    def test_exactly_100k_not_integer(self):
        # 100,000 is not > 100,000
        assert round_price(100_000.0) == 100000.0

    def test_large_price(self):
        assert round_price(250_000.75) == 250001.0

    def test_large_price_with_sz_decimals(self):
        # sz_decimals should be irrelevant for >100k
        assert round_price(150_000.49, sz_decimals=3) == 150000.0

    def test_returns_float_not_int(self):
        result = round_price(100_001.4)
        assert result == 100001.0
        assert isinstance(result, float)


class TestHIP3SpotRounding:
    """HIP-3 (spot) coins use is_perp=False with max_decimals=8."""

    def test_spot_more_precision_than_perp(self):
        # Same price + sz_decimals, spot allows more decimal places
        price = 0.00123456
        perp_result = round_price(price, sz_decimals=2, is_perp=True)   # 6-2=4 dp
        spot_result = round_price(price, sz_decimals=2, is_perp=False)  # 8-2=6 dp
        assert spot_result != perp_result
        assert spot_result == 0.001235   # more precise
        assert perp_result == 0.0012     # less precise

    def test_hip3_coin_sz_decimals_3(self):
        # Typical HIP-3 coin: sz_decimals=3, is_perp=False
        # 8 - 3 = 5 decimal places
        assert round_price(1.2345678, sz_decimals=3, is_perp=False) == 1.2346


class TestGetSzDecimals:
    """OrderManager._get_sz_decimals with caching and fallback."""

    def _make_om(self, meta_response=None, raise_error=False):
        exchange = MagicMock()
        info = MagicMock()
        if raise_error:
            info.meta.side_effect = Exception("API error")
        else:
            info.meta.return_value = meta_response or {}
        om = OrderManager(exchange, info, "0xabc")
        return om

    @patch('order_manager.api_wrapper')
    def test_returns_sz_decimals_from_meta(self, mock_wrapper):
        meta = {'universe': [{'name': 'BTC', 'szDecimals': 5}]}
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        om = self._make_om(meta)
        assert om._get_sz_decimals('BTC') == 5

    @patch('order_manager.api_wrapper')
    def test_returns_default_3_when_not_found(self, mock_wrapper):
        meta = {'universe': [{'name': 'ETH', 'szDecimals': 2}]}
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        om = self._make_om(meta)
        assert om._get_sz_decimals('BTC') == 3

    @patch('order_manager.api_wrapper')
    def test_returns_default_3_on_error(self, mock_wrapper):
        mock_wrapper.call.side_effect = ConnectionError("API error")
        om = self._make_om()
        assert om._get_sz_decimals('BTC') == 3

    @patch('order_manager.api_wrapper')
    def test_caches_result(self, mock_wrapper):
        meta = {'universe': [{'name': 'BTC', 'szDecimals': 5}]}
        mock_wrapper.call.side_effect = lambda fn, *a, **kw: fn(*a, **kw)
        om = self._make_om(meta)
        om._get_sz_decimals('BTC')
        om._get_sz_decimals('BTC')
        # Second call should use cache, so info.meta called only once
        assert mock_wrapper.call.call_count == 1
