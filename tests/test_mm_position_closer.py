"""Tests for PositionCloser -- aging close tiers.

Tests the progressive close tightening: positions that age past 50%
and 75% of max_position_age get their close orders repriced at breakeven
and small-loss levels respectively, reducing taker force-closes.
"""

import time
from unittest.mock import MagicMock

from strategies.mm_position_closer import (
    PositionCloser,
    _TIER_AGGRESSIVE,
    _TIER_BREAKEVEN,
    _TIER_NORMAL,
)


def _make_closer(max_age=120, maker_only=True, taker_fallback=None,
                 spread_bps=10):
    om = MagicMock()
    md = MagicMock()
    md.round_size.return_value = 0.5
    md.get_sz_decimals.return_value = 0
    md.price_rounding_params.return_value = (0, True)
    closer = PositionCloser(
        order_manager=om,
        market_data=md,
        spread_bps=spread_bps,
        max_position_age_seconds=max_age,
        maker_only=maker_only,
        taker_fallback_age_seconds=taker_fallback,
    )
    return closer, om, md


class TestGetTier:
    """_get_tier returns correct tier based on position age."""

    def test_normal_tier_early(self):
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier(0) == _TIER_NORMAL
        assert closer._get_tier(30) == _TIER_NORMAL
        assert closer._get_tier(59) == _TIER_NORMAL

    def test_breakeven_tier_at_50pct(self):
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier(60) == _TIER_BREAKEVEN
        assert closer._get_tier(80) == _TIER_BREAKEVEN
        assert closer._get_tier(89) == _TIER_BREAKEVEN

    def test_aggressive_tier_at_75pct(self):
        closer, _, _ = _make_closer(max_age=120)
        assert closer._get_tier(90) == _TIER_AGGRESSIVE
        assert closer._get_tier(110) == _TIER_AGGRESSIVE
        assert closer._get_tier(119) == _TIER_AGGRESSIVE


class TestTierSpreadBps:
    """_tier_spread_bps returns correct spread for each tier."""

    def test_normal_tier_uses_configured_spread(self):
        closer, _, _ = _make_closer(spread_bps=10)
        assert closer._tier_spread_bps(_TIER_NORMAL) == 10

    def test_breakeven_tier_zero_spread(self):
        closer, _, _ = _make_closer(spread_bps=10)
        assert closer._tier_spread_bps(_TIER_BREAKEVEN) == 0.0

    def test_aggressive_tier_negative_spread(self):
        closer, _, _ = _make_closer(spread_bps=10)
        assert closer._tier_spread_bps(_TIER_AGGRESSIVE) == -1.0


class TestTightenClose:
    """manage() cancels and re-places close order when tier transitions."""

    def test_tightens_at_breakeven_threshold(self):
        """When position ages past 50%, close order should be cancelled
        and re-placed at breakeven."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        # Position placed 65s ago, currently at TIER_NORMAL with order
        entry_time = time.monotonic() - 65
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_NORMAL)

        # Close order is still alive
        om.get_open_orders.return_value = [{'oid': 42}]

        mock_order = MagicMock()
        mock_order.id = 99
        om.create_limit_order.return_value = mock_order

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should have cancelled old order
        om.cancel_order.assert_called_once_with(42, 'BTC')
        # Should have placed new order
        om.create_limit_order.assert_called_once()
        # New order should be tracked at BREAKEVEN tier
        assert closer._open_positions['BTC'][2] == _TIER_BREAKEVEN

    def test_tightens_at_aggressive_threshold(self):
        """When position ages past 75%, close order should be cancelled
        and re-placed at loss-cut price."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        # Position placed 95s ago, at TIER_BREAKEVEN with close order
        entry_time = time.monotonic() - 95
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_BREAKEVEN)

        # Close order is still alive
        om.get_open_orders.return_value = [{'oid': 42}]

        mock_order = MagicMock()
        mock_order.id = 99
        om.create_limit_order.return_value = mock_order

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should have cancelled old order
        om.cancel_order.assert_called_once_with(42, 'BTC')
        # Should have placed new order
        om.create_limit_order.assert_called_once()
        # New order should be tracked at AGGRESSIVE tier
        assert closer._open_positions['BTC'][2] == _TIER_AGGRESSIVE

    def test_no_tighten_when_already_at_correct_tier(self):
        """When already at BREAKEVEN tier and still in breakeven range,
        should NOT cancel and re-place."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        # Position at 65s, already at BREAKEVEN tier
        entry_time = time.monotonic() - 65
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_BREAKEVEN)

        # Close order is still alive
        om.get_open_orders.return_value = [{'oid': 42}]

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should NOT have cancelled or placed any order
        om.cancel_order.assert_not_called()
        om.create_limit_order.assert_not_called()

    def test_no_tighten_in_normal_period(self):
        """During normal period (< 50% age), should not tighten."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        # Position at 30s, at NORMAL tier
        entry_time = time.monotonic() - 30
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_NORMAL)

        # Close order is still alive
        om.get_open_orders.return_value = [{'oid': 42}]

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should NOT have cancelled or placed any order
        om.cancel_order.assert_not_called()
        om.create_limit_order.assert_not_called()

    def test_skips_tier_if_cancel_fails(self):
        """If cancel fails, should return without placing new order."""
        closer, om, md = _make_closer(max_age=120, maker_only=False)

        entry_time = time.monotonic() - 65
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_NORMAL)

        om.get_open_orders.return_value = [{'oid': 42}]
        om.cancel_order.side_effect = ConnectionError("API error")

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Should NOT have placed any new order
        om.create_limit_order.assert_not_called()


class TestClosePriceCalculation:
    """Close price reflects the tier spread correctly."""

    def test_normal_tier_long_close_price(self):
        """For a long at normal tier, close price = entry * (1 + spread)."""
        closer, om, md = _make_closer(max_age=120, spread_bps=10, maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic()  # just now, age ~0
        closer._place_take_profit('BTC', 0.5, 50000.0, entry_time, _TIER_NORMAL)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # 50000 * (1 + 10/10000) = 50050
        assert abs(call_kwargs['price'] - 50050.0) < 1.0

    def test_breakeven_tier_long_close_price(self):
        """For a long at breakeven tier, close price = entry."""
        closer, om, md = _make_closer(max_age=120, spread_bps=10, maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic() - 65  # past 50%
        closer._place_take_profit('BTC', 0.5, 50000.0, entry_time, _TIER_BREAKEVEN)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # 50000 * (1 + 0/10000) = 50000
        assert abs(call_kwargs['price'] - 50000.0) < 1.0

    def test_aggressive_tier_long_close_below_entry(self):
        """For a long at aggressive tier, close price < entry."""
        closer, om, md = _make_closer(max_age=120, spread_bps=10, maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic() - 95  # past 75%
        closer._place_take_profit('BTC', 0.5, 50000.0, entry_time, _TIER_AGGRESSIVE)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # 50000 * (1 + (-1)/10000) = 50000 * 0.9999 = 49995
        assert call_kwargs['price'] < 50000.0

    def test_aggressive_tier_short_close_above_entry(self):
        """For a short at aggressive tier, close price > entry."""
        closer, om, md = _make_closer(max_age=120, spread_bps=10, maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        entry_time = time.monotonic() - 95
        closer._place_take_profit('BTC', -0.5, 50000.0, entry_time, _TIER_AGGRESSIVE)

        call_kwargs = om.create_limit_order.call_args.kwargs
        # 50000 * (1 - (-1)/10000) = 50000 * 1.0001 = 50005
        assert call_kwargs['price'] > 50000.0


class TestManageBasicBehavior:
    """Basic manage() behavior is preserved."""

    def test_new_position_tracked_at_normal_tier(self):
        """New positions start at TIER_NORMAL."""
        closer, om, md = _make_closer(maker_only=False)

        mock_order = MagicMock()
        mock_order.id = 1
        om.create_limit_order.return_value = mock_order

        position = {'size': 0.5, 'entry_price': 50000.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        assert 'BTC' in closer._open_positions
        assert closer._open_positions['BTC'][2] == _TIER_NORMAL

    def test_force_close_at_max_age(self):
        """Taker force-close fires at max_position_age."""
        closer, om, _ = _make_closer(max_age=60, maker_only=False)

        entry_time = time.monotonic() - 120  # well past max_age
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 50000.0}

        closer.manage('BTC', position, close_fn)

        close_fn.assert_called_once_with('BTC')
        assert 'BTC' not in closer._open_positions

    def test_force_close_maker_tracks_aggressive_tier(self):
        """Maker-only force-close stores AGGRESSIVE tier."""
        closer, om, md = _make_closer(max_age=60, maker_only=True)

        entry_time = time.monotonic() - 120
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        md_obj = MagicMock()
        md_obj.mid_price = 50000.0
        md_obj.bid = 49999.0
        md_obj.ask = 50001.0
        md.get_market_data.return_value = md_obj

        mock_order = MagicMock()
        mock_order.id = 99
        om.create_limit_order.return_value = mock_order

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 50000.0}

        closer.manage('BTC', position, close_fn)

        # Should not have used taker (maker_only=True, no taker_fallback)
        close_fn.assert_not_called()
        # Should have stored AGGRESSIVE tier
        assert closer._open_positions['BTC'][2] == _TIER_AGGRESSIVE
