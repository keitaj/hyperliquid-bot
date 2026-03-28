"""Tests for coin_utils: HIP-3 coin notation helpers."""

from coin_utils import is_hip3, parse_coin, make_hip3_coin


class TestIsHip3:

    def test_standard_coin(self):
        assert is_hip3("BTC") is False
        assert is_hip3("ETH") is False

    def test_hip3_coin(self):
        assert is_hip3("xyz:GOLD") is True
        assert is_hip3("flx:NVDA") is True

    def test_empty_string(self):
        assert is_hip3("") is False


class TestParseCoin:

    def test_standard_coin(self):
        dex, name = parse_coin("BTC")
        assert dex is None
        assert name == "BTC"

    def test_hip3_coin(self):
        dex, name = parse_coin("xyz:GOLD")
        assert dex == "xyz"
        assert name == "GOLD"

    def test_maxsplit_handles_extra_colons(self):
        dex, name = parse_coin("xyz:GOLD:extra")
        assert dex == "xyz"
        assert name == "GOLD:extra"

    def test_empty_string(self):
        dex, name = parse_coin("")
        assert dex is None
        assert name == ""


class TestMakeHip3Coin:

    def test_bare_coin(self):
        assert make_hip3_coin("xyz", "GOLD") == "xyz:GOLD"

    def test_already_prefixed(self):
        assert make_hip3_coin("xyz", "xyz:GOLD") == "xyz:GOLD"

    def test_different_prefix_already_present(self):
        # If coin already has a prefix, keep it as-is
        assert make_hip3_coin("flx", "xyz:GOLD") == "xyz:GOLD"

    def test_empty_coin_name(self):
        assert make_hip3_coin("xyz", "") == "xyz:"
