"""Tests for round_price with sz_decimals and is_perp support."""

from order_manager import round_price


class TestRoundPriceDefaults:
    """Default behaviour (sz_decimals=0, is_perp=True) matches legacy."""

    def test_five_sig_figs(self):
        assert round_price(12345.6789) == 12346.0

    def test_small_price(self):
        assert round_price(0.00123456) == 0.001235

    def test_six_decimal_places_cap(self):
        # 5 sig figs: 0.0012345 → "0.0012345", then round to 6 dp → 0.001234
        assert round_price(0.0012345) == 0.001234


class TestSzDecimalsPerp:
    """Perp prices use max_decimals=6, so price decimals = 6 - sz_decimals."""

    def test_sz_decimals_0(self):
        # 6 - 0 = 6 decimal places
        assert round_price(1.23456789, sz_decimals=0) == 1.2346

    def test_sz_decimals_2(self):
        # 6 - 2 = 4 decimal places
        assert round_price(1.23456789, sz_decimals=2) == 1.2346

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
        # 8 - 4 = 4 decimal places
        assert round_price(1.23456789, sz_decimals=4, is_perp=False) == 1.2346


class TestHighPriceIntegerRule:
    """Prices above 100,000 are always rounded to integers."""

    def test_above_100k_rounds_to_int(self):
        assert round_price(100_001.99) == 100002

    def test_exactly_100k_not_integer(self):
        # 100,000 is not > 100,000
        assert round_price(100_000.0) == 100000.0

    def test_large_price(self):
        assert round_price(250_000.75) == 250001

    def test_large_price_with_sz_decimals(self):
        # sz_decimals should be irrelevant for >100k
        assert round_price(150_000.49, sz_decimals=3) == 150000

    def test_returns_int_valued_float(self):
        result = round_price(100_001.4)
        assert result == 100001
