"""Tests for position_closer.close_position_market."""

from unittest.mock import MagicMock
from order_manager import OrderSide
from position_closer import close_position_market


def _make_deps(sz_decimals=3, order_succeeds=True):
    market_data = MagicMock()
    market_data.round_size.side_effect = (
        lambda coin, size: round(size, sz_decimals)
    )
    order_manager = MagicMock()
    order_manager.create_market_order.return_value = (
        MagicMock() if order_succeeds else None
    )
    return market_data, order_manager


class TestClosePositionMarket:

    def test_close_long(self):
        md, om = _make_deps()
        result = close_position_market('BTC', 0.5, md, om)

        assert result is True
        om.create_market_order.assert_called_once_with(
            coin='BTC', side=OrderSide.SELL, size=0.5, reduce_only=True,
        )

    def test_close_short(self):
        md, om = _make_deps()
        result = close_position_market('ETH', -2.0, md, om)

        assert result is True
        om.create_market_order.assert_called_once_with(
            coin='ETH', side=OrderSide.BUY, size=2.0, reduce_only=True,
        )

    def test_zero_size_returns_false(self):
        md, om = _make_deps()
        result = close_position_market('SOL', 0, md, om)

        assert result is False
        om.create_market_order.assert_not_called()

    def test_order_failure_returns_false(self):
        md, om = _make_deps(order_succeeds=False)
        result = close_position_market('BTC', 1.0, md, om)

        assert result is False

    def test_size_rounded(self):
        md, om = _make_deps(sz_decimals=2)
        close_position_market('BTC', 0.12345, md, om)

        call_args = om.create_market_order.call_args
        assert call_args.kwargs['size'] == 0.12

    def test_reason_does_not_affect_result(self):
        md, om = _make_deps()
        result = close_position_market(
            'BTC', 1.0, md, om, reason='Per-trade stop loss',
        )
        assert result is True
