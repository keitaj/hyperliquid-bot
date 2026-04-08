"""Tests for zero-size position guard in PositionCloser.manage()."""

import time
from unittest.mock import MagicMock

from strategies.mm_position_closer import PositionCloser


def _make_closer():
    om = MagicMock()
    md = MagicMock()
    closer = PositionCloser(
        order_manager=om,
        market_data=md,
        spread_bps=10,
        max_position_age_seconds=120,
        maker_only=True,
        taker_fallback_age_seconds=None,
    )
    return closer, om, md


class TestZeroSizeWithTrackedPosition:
    def test_cleanup_called_and_tracking_removed(self):
        closer, om, _ = _make_closer()
        # Simulate a tracked position with an active close order
        closer._open_positions['BTC'] = (time.monotonic() - 30, 42)

        closer.manage('BTC', {'size': 0, 'entry_price': 50000.0}, MagicMock())

        assert 'BTC' not in closer._open_positions
        om.cancel_order.assert_called_once_with(42, 'BTC')
        om.create_limit_order.assert_not_called()

    def test_close_fn_not_called(self):
        closer, _, _ = _make_closer()
        closer._open_positions['ETH'] = (time.monotonic() - 10, None)
        close_fn = MagicMock()

        closer.manage('ETH', {'size': 0, 'entry_price': 3000.0}, close_fn)

        close_fn.assert_not_called()


class TestZeroSizeWithoutTracking:
    def test_noop_when_not_tracked(self):
        closer, om, _ = _make_closer()
        close_fn = MagicMock()

        closer.manage('BTC', {'size': 0, 'entry_price': 50000.0}, close_fn)

        assert 'BTC' not in closer._open_positions
        om.create_limit_order.assert_not_called()
        close_fn.assert_not_called()


class TestMissingSizeKey:
    def test_missing_size_treated_as_zero(self):
        closer, om, _ = _make_closer()

        closer.manage('BTC', {'entry_price': 50000.0}, MagicMock())

        assert 'BTC' not in closer._open_positions
        om.create_limit_order.assert_not_called()
