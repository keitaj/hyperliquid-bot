"""Tests for close order optimization: spread, tier timing, and BBO tracking."""

import time
from unittest.mock import MagicMock
from dataclasses import dataclass
from datetime import datetime

from strategies.mm_position_closer import PositionCloser

_TIER_NORMAL = 0
_TIER_BREAKEVEN = 1
_TIER_AGGRESSIVE = 2


def _make_closer(**kwargs):
    om = MagicMock()
    md = MagicMock()
    om.get_all_positions.return_value = [{'coin': 'SP500', 'szi': '0.5'}]
    defaults = dict(
        order_manager=om, market_data=md,
        spread_bps=10, max_position_age_seconds=120,
        maker_only=True, taker_fallback_age_seconds=120,
    )
    defaults.update(kwargs)
    closer = PositionCloser(**defaults)
    return closer, om, md


class TestCloseSpreadBps:
    """Test improvement A: configurable close_spread_bps."""

    def test_default_uses_entry_spread(self):
        """When close_spread_bps is None, uses spread_bps (backward compat)."""
        closer, _, _ = _make_closer(spread_bps=10)
        assert closer.close_spread_bps == 10

    def test_custom_close_spread(self):
        """close_spread_bps overrides spread_bps for close orders."""
        closer, _, _ = _make_closer(spread_bps=10, close_spread_bps=3)
        assert closer.close_spread_bps == 3

    def test_tier_normal_uses_close_spread(self):
        """TIER_NORMAL uses close_spread_bps, not spread_bps."""
        closer, _, _ = _make_closer(spread_bps=10, close_spread_bps=3)
        assert closer._tier_spread_bps(_TIER_NORMAL) == 3

    def test_tier_breakeven_unaffected(self):
        """TIER_BREAKEVEN is always 0 regardless of close_spread_bps."""
        closer, _, _ = _make_closer(spread_bps=10, close_spread_bps=3)
        assert closer._tier_spread_bps(_TIER_BREAKEVEN) == 0.0

    def test_tier_aggressive_unaffected(self):
        """TIER_AGGRESSIVE uses aggressive_loss_bps, not close_spread."""
        closer, _, _ = _make_closer(spread_bps=10, close_spread_bps=3, aggressive_loss_bps=1.5)
        assert closer._tier_spread_bps(_TIER_AGGRESSIVE) == -1.5


class TestTierTransitionTiming:
    """Test improvement B: configurable tier transition timing."""

    def test_default_timing(self):
        """Default: breakeven at 50%, aggressive at 75%."""
        closer, _, _ = _make_closer(max_position_age_seconds=120)
        # age=59s → NORMAL
        assert closer._get_tier(59) == _TIER_NORMAL
        # age=60s → BREAKEVEN (120 * 0.50)
        assert closer._get_tier(60) == _TIER_BREAKEVEN
        # age=89s → BREAKEVEN
        assert closer._get_tier(89) == _TIER_BREAKEVEN
        # age=90s → AGGRESSIVE (120 * 0.75)
        assert closer._get_tier(90) == _TIER_AGGRESSIVE

    def test_early_transition(self):
        """Earlier tier transitions with custom percentages."""
        closer, _, _ = _make_closer(
            max_position_age_seconds=120,
            close_breakeven_pct=0.33,
            close_aggressive_pct=0.50,
        )
        # age=39s → NORMAL (120 * 0.33 = 39.6)
        assert closer._get_tier(39) == _TIER_NORMAL
        # age=40s → BREAKEVEN
        assert closer._get_tier(40) == _TIER_BREAKEVEN
        # age=59s → BREAKEVEN
        assert closer._get_tier(59) == _TIER_BREAKEVEN
        # age=60s → AGGRESSIVE (120 * 0.50)
        assert closer._get_tier(60) == _TIER_AGGRESSIVE


class TestBboTrackingClosePrice:
    """Test improvement C: BBO-tracking close pricing."""

    def test_bbo_closer_than_entry_uses_bbo(self):
        """When BBO is closer to market than entry+spread, use BBO."""
        closer, om, md = _make_closer(spread_bps=10, close_spread_bps=10, maker_only=True)

        @dataclass
        class MockMD:
            bid: float = 99.9
            ask: float = 100.1
            mid_price: float = 100.0
            symbol: str = "SP500"
            spread: float = 0.2
            timestamp: object = None
            book_imbalance: float = 0.0
            bid_size_top: float = 10.0
            ask_size_top: float = 10.0
            micro_price: float = 100.0

        md.get_market_data.return_value = MockMD(timestamp=datetime.now())
        md.price_rounding_params.return_value = (3, True)
        md.round_size.return_value = 0.5

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic()
        # Long position: entry=100, spread=10bps → entry_close=100.10
        # BBO: ask=100.1, bbo_close = ask*(1+0.0001) ≈ 100.1101
        # min(100.10, 100.1101) = 100.10 → entry wins (closer to market)
        closer._open_positions['SP500'] = (entry_time, None, _TIER_NORMAL)
        closer._place_take_profit('SP500', 0.5, 100.0, entry_time, _TIER_NORMAL)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # Entry-based: 100 * (1 + 10/10000) = 100.10
        assert call_kwargs['price'] <= 100.11

    def test_entry_farther_than_bbo_uses_bbo(self):
        """When entry+spread is farther than BBO, use BBO price."""
        closer, om, md = _make_closer(spread_bps=50, close_spread_bps=50, maker_only=True)

        @dataclass
        class MockMD:
            bid: float = 99.9
            ask: float = 100.1
            mid_price: float = 100.0
            symbol: str = "SP500"
            spread: float = 0.2
            timestamp: object = None
            book_imbalance: float = 0.0
            bid_size_top: float = 10.0
            ask_size_top: float = 10.0
            micro_price: float = 100.0

        md.get_market_data.return_value = MockMD(timestamp=datetime.now())
        md.price_rounding_params.return_value = (3, True)
        md.round_size.return_value = 0.5

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic()
        # Long position: entry=100, spread=50bps → entry_close=100.50
        # BBO: ask=100.1, bbo_close = ask*(1+0.0001) ≈ 100.11
        # min(100.50, 100.11) = 100.11 → BBO wins (closer to market)
        closer._open_positions['SP500'] = (entry_time, None, _TIER_NORMAL)
        closer._place_take_profit('SP500', 0.5, 100.0, entry_time, _TIER_NORMAL)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # Should use BBO-based price (~100.11), not entry-based (100.50)
        assert call_kwargs['price'] < 100.20

    def test_no_market_data_uses_entry(self):
        """When market data unavailable, fall back to entry-based pricing."""
        closer, om, md = _make_closer(spread_bps=10, close_spread_bps=10, maker_only=False)
        md.get_market_data.return_value = None
        md.price_rounding_params.return_value = (3, True)
        md.round_size.return_value = 0.5

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic()
        closer._open_positions['SP500'] = (entry_time, None, _TIER_NORMAL)
        closer._place_take_profit('SP500', 0.5, 100.0, entry_time, _TIER_NORMAL)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # Entry-based: 100 * (1 + 10/10000) = 100.10
        assert abs(call_kwargs['price'] - 100.10) < 0.01

    def test_short_position_bbo_tracking(self):
        """For short positions, use max(entry, bbo) — higher is more aggressive."""
        closer, om, md = _make_closer(spread_bps=50, close_spread_bps=50, maker_only=True)

        @dataclass
        class MockMD:
            bid: float = 99.9
            ask: float = 100.1
            mid_price: float = 100.0
            symbol: str = "SP500"
            spread: float = 0.2
            timestamp: object = None
            book_imbalance: float = 0.0
            bid_size_top: float = 10.0
            ask_size_top: float = 10.0
            micro_price: float = 100.0

        md.get_market_data.return_value = MockMD(timestamp=datetime.now())
        md.price_rounding_params.return_value = (3, True)
        md.round_size.return_value = 0.5

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic()
        # Short position: entry=100, spread=50bps → entry_close = 100*(1-50/10000) = 99.50
        # BBO: bid=99.9, bbo_close = bid*(1-0.0001) ≈ 99.89
        # max(99.50, 99.89) = 99.89 → BBO wins (closer to market for buy-to-close)
        closer._open_positions['SP500'] = (entry_time, None, _TIER_NORMAL)
        closer._place_take_profit('SP500', -0.5, 100.0, entry_time, _TIER_NORMAL)

        call_kwargs = om.create_limit_order.call_args.kwargs
        assert call_kwargs['price'] > 99.80  # Should be near 99.89, not 99.50
