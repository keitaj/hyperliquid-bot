"""Tests for BBO price validation in maker-only mode."""

import time
from unittest.mock import MagicMock

from strategies.mm_position_closer import PositionCloser, _BBO_OFFSET
from order_manager import OrderSide, round_price


def _make_closer(maker_only=True, spread_bps=10, max_age=120):
    om = MagicMock()
    md = MagicMock()
    md.round_size.return_value = 0.5
    closer = PositionCloser(
        order_manager=om,
        market_data=md,
        spread_bps=spread_bps,
        max_position_age_seconds=max_age,
        maker_only=maker_only,
        taker_fallback_age_seconds=None,
    )
    return closer, om, md


class TestTakeProfitBBOClamping:
    """_place_take_profit clamps price to BBO instead of placing inside spread."""

    def test_sell_clamped_above_ask(self):
        closer, om, md = _make_closer(spread_bps=5)
        # ask is above the take-profit price
        market_data = MagicMock()
        market_data.bid = 100.00
        market_data.ask = 100.02
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic()
        # entry at 100.01, spread 5bps → take-profit sell at 100.06
        # but ask is 100.02, so 100.06 > ask → no clamping needed
        closer._place_take_profit('BTC', 1.0, 100.01, entry_time)
        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        assert call_kwargs['price'] > market_data.ask

    def test_sell_clamped_when_crossing(self):
        closer, om, md = _make_closer(spread_bps=1)
        # Very tight spread: take-profit would land inside ask
        market_data = MagicMock()
        market_data.bid = 100.00
        market_data.ask = 100.05
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic()
        # entry at 100.00, spread 1bps → take-profit sell at ~100.01
        # ask is 100.05, so 100.01 < ask → must clamp to ask + offset
        closer._place_take_profit('BTC', 1.0, 100.00, entry_time)
        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        assert call_kwargs['price'] > market_data.ask

    def test_buy_clamped_when_crossing(self):
        closer, om, md = _make_closer(spread_bps=1)
        market_data = MagicMock()
        market_data.bid = 99.95
        market_data.ask = 100.00
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic()
        # short entry at 100.00, spread 1bps → take-profit buy at ~99.99
        # bid is 99.95, so 99.99 > bid → must clamp to bid - offset
        closer._place_take_profit('BTC', -1.0, 100.00, entry_time)
        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        assert call_kwargs['price'] < market_data.bid


class TestForceCloseBBOPricing:
    """_handle_force_close uses BBO-aware pricing."""

    def test_sell_at_ask_plus_offset(self):
        closer, om, md = _make_closer()
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 99.99
        market_data.ask = 100.01
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic() - 200
        closer._open_positions['BTC'] = (entry_time, None)
        closer._handle_force_close('BTC', 1.0, 200, entry_time, None, MagicMock())

        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        expected = round_price(100.01 * (1 + _BBO_OFFSET))
        assert call_kwargs['price'] == expected
        assert call_kwargs['side'] == OrderSide.SELL

    def test_buy_at_bid_minus_offset(self):
        closer, om, md = _make_closer()
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 99.99
        market_data.ask = 100.01
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic() - 200
        closer._open_positions['BTC'] = (entry_time, None)
        closer._handle_force_close('BTC', -1.0, 200, entry_time, None, MagicMock())

        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args[1]
        expected = round_price(99.99 * (1 - _BBO_OFFSET))
        assert call_kwargs['price'] == expected
        assert call_kwargs['side'] == OrderSide.BUY

    def test_skips_when_no_bbo(self):
        closer, om, md = _make_closer()
        market_data = MagicMock()
        market_data.mid_price = 100.00
        market_data.bid = 0
        market_data.ask = 0
        md.get_market_data.return_value = market_data

        entry_time = time.monotonic() - 200
        closer._handle_force_close('BTC', 1.0, 200, entry_time, None, MagicMock())

        om.create_limit_order.assert_not_called()
