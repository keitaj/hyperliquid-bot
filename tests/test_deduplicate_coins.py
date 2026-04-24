"""Tests for HyperliquidBot._deduplicate_coins().

Verifies that duplicate coins are detected, removed, and reported
while preserving the order of first occurrence.
"""

from bot import HyperliquidBot


class TestDeduplicateCoins:

    def test_no_duplicates(self):
        unique, dups = HyperliquidBot._deduplicate_coins(
            ['xyz:SP500', 'xyz:NVDA', 'xyz:GOOGL']
        )
        assert unique == ['xyz:SP500', 'xyz:NVDA', 'xyz:GOOGL']
        assert dups == []

    def test_single_duplicate(self):
        unique, dups = HyperliquidBot._deduplicate_coins(
            ['xyz:SP500', 'xyz:TSLA', 'xyz:TSLA', 'xyz:AAPL']
        )
        assert unique == ['xyz:SP500', 'xyz:TSLA', 'xyz:AAPL']
        assert dups == ['xyz:TSLA']

    def test_multiple_duplicates(self):
        unique, dups = HyperliquidBot._deduplicate_coins(
            ['xyz:SP500', 'xyz:TSLA', 'xyz:NVDA', 'xyz:TSLA', 'xyz:NVDA']
        )
        assert unique == ['xyz:SP500', 'xyz:TSLA', 'xyz:NVDA']
        assert dups == ['xyz:TSLA', 'xyz:NVDA']

    def test_preserves_order(self):
        unique, dups = HyperliquidBot._deduplicate_coins(
            ['xyz:GOOGL', 'xyz:SP500', 'xyz:GOOGL', 'xyz:NVDA']
        )
        assert unique == ['xyz:GOOGL', 'xyz:SP500', 'xyz:NVDA']
        assert unique[0] == 'xyz:GOOGL'  # first occurrence preserved

    def test_empty_list(self):
        unique, dups = HyperliquidBot._deduplicate_coins([])
        assert unique == []
        assert dups == []

    def test_all_same(self):
        unique, dups = HyperliquidBot._deduplicate_coins(
            ['xyz:SP500', 'xyz:SP500', 'xyz:SP500']
        )
        assert unique == ['xyz:SP500']
        assert dups == ['xyz:SP500', 'xyz:SP500']

    def test_mixed_hl_and_hip3(self):
        unique, dups = HyperliquidBot._deduplicate_coins(
            ['BTC', 'ETH', 'xyz:SP500', 'BTC', 'xyz:SP500']
        )
        assert unique == ['BTC', 'ETH', 'xyz:SP500']
        assert dups == ['BTC', 'xyz:SP500']

    def test_case_sensitive(self):
        """Coin names are case-sensitive — xyz:sp500 != xyz:SP500."""
        unique, dups = HyperliquidBot._deduplicate_coins(
            ['xyz:SP500', 'xyz:sp500']
        )
        assert unique == ['xyz:SP500', 'xyz:sp500']
        assert dups == []
