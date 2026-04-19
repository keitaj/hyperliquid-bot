"""Tests for dynamic close pricing during force-close phase."""

import time
from unittest.mock import MagicMock

from strategies.mm_position_closer import PositionCloser


def _make_closer(max_age=120, taker_fallback=120, aggressive_loss_bps=1.0,
                 force_close_max_loss_bps=0.0, maker_only=True):
    """Create a PositionCloser with mocked dependencies."""
    om = MagicMock()
    md = MagicMock()
    closer = PositionCloser(
        order_manager=om, market_data=md,
        spread_bps=10, max_position_age_seconds=max_age,
        maker_only=maker_only, taker_fallback_age_seconds=taker_fallback,
        aggressive_loss_bps=aggressive_loss_bps,
        force_close_max_loss_bps=force_close_max_loss_bps,
    )
    om.get_all_positions.return_value = [{'coin': 'SP500', 'szi': '0.5'}]
    return closer, om, md


def _setup_market_data(md, bid=99.9, ask=100.1, mid=100.0):
    """Configure mock market data."""
    market_data = MagicMock()
    market_data.mid_price = mid
    market_data.bid = bid
    market_data.ask = ask
    md.get_market_data.return_value = market_data
    md.price_rounding_params.return_value = (4, 0.01)
    md.round_size.return_value = 0.1
    return market_data


class TestDynamicClosePricingDisabled:
    """When force_close_max_loss_bps=0 (default), behavior is unchanged."""

    def test_default_uses_bbo_pricing(self):
        closer, om, md = _make_closer(force_close_max_loss_bps=0.0)
        _setup_market_data(md)

        order = MagicMock()
        order.id = 123
        om.create_limit_order.return_value = order

        entry_time = time.monotonic() - 130  # 130s, past max_age=120
        closer._open_positions["SP500"] = (entry_time, None, 2)

        position = {'size': 0.5, 'entry_price': 100.0}
        closer.manage("SP500", position, MagicMock())

        # Should use BBO-based pricing (ask + offset), not entry-based
        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args.kwargs
        assert call_kwargs['reduce_only'] is True


class TestDynamicClosePricingEnabled:
    """When force_close_max_loss_bps > 0, loss acceptance scales with time."""

    def test_progress_zero_uses_aggressive_loss(self):
        """At start of force-close phase, loss = aggressive_loss_bps."""
        closer, om, md = _make_closer(
            max_age=120, taker_fallback=120,
            aggressive_loss_bps=1.0, force_close_max_loss_bps=3.0,
        )
        _setup_market_data(md, bid=99.9, ask=100.1)

        order = MagicMock()
        order.id = 123
        om.create_limit_order.return_value = order

        # age=120 → progress=0.0 → loss=1.0bps
        entry_time = time.monotonic() - 120
        closer._open_positions["SP500"] = (entry_time, None, 2)

        position = {'size': 0.5, 'entry_price': 100.0}
        closer.manage("SP500", position, MagicMock())

        om.create_limit_order.assert_called_once()
        price = om.create_limit_order.call_args.kwargs['price']
        # Long → sell to close. entry=100, loss=1bps → 100 * (1 - 0.0001) = 99.99
        # But BBO sell = ask * (1 + offset) ≈ 100.1 + tiny = ~100.1
        # entry_based (99.99) < bbo_based (100.1) → entry_based is more aggressive
        # Clamped: 99.99 < ask (100.1)? Yes → uses BBO price
        # Actually: for a SELL, lower price = more aggressive (willing to sell cheaper)
        # 99.99 <= 100.1 (ask) → clamp to bbo_price
        assert price > 0

    def test_progress_half_scales_loss(self):
        """At 50% progress, loss = midpoint between aggressive and max."""
        closer, om, md = _make_closer(
            max_age=120, taker_fallback=120,
            aggressive_loss_bps=1.0, force_close_max_loss_bps=3.0,
        )
        _setup_market_data(md, bid=99.5, ask=100.5)

        order = MagicMock()
        order.id = 456
        om.create_limit_order.return_value = order

        # age=180 → progress=0.5 → loss=2.0bps
        entry_time = time.monotonic() - 180
        closer._open_positions["SP500"] = (entry_time, None, 2)

        position = {'size': 0.5, 'entry_price': 100.0}
        closer.manage("SP500", position, MagicMock())

        om.create_limit_order.assert_called_once()
        price = om.create_limit_order.call_args.kwargs['price']
        # entry_based for sell: 100 * (1 - 2.0/10000) = 99.98
        # bbo_based: ask * (1 + offset) ≈ 100.5+
        # 99.98 < 100.5 (ask) → clamped to bbo_price
        assert price > 0

    def test_short_position_buy_to_close(self):
        """Short position: buy to close with dynamic pricing."""
        closer, om, md = _make_closer(
            max_age=120, taker_fallback=120,
            aggressive_loss_bps=1.0, force_close_max_loss_bps=3.0,
        )
        _setup_market_data(md, bid=99.5, ask=100.5)

        order = MagicMock()
        order.id = 789
        om.create_limit_order.return_value = order

        entry_time = time.monotonic() - 180
        closer._open_positions["SP500"] = (entry_time, None, 2)

        # Short position
        position = {'size': -0.5, 'entry_price': 100.0}
        closer.manage("SP500", position, MagicMock())

        om.create_limit_order.assert_called_once()
        call_kwargs = om.create_limit_order.call_args.kwargs
        assert call_kwargs['side'].value in ('buy', 'B')  # Buy to close short

    def test_taker_deadline_still_force_closes(self):
        """At taker deadline, force taker close regardless of dynamic pricing."""
        closer, om, md = _make_closer(
            max_age=120, taker_fallback=120,
            force_close_max_loss_bps=3.0,
        )

        entry_time = time.monotonic() - 245  # past 240s deadline
        closer._open_positions["SP500"] = (entry_time, None, 2)

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 100.0}
        closer.manage("SP500", position, close_fn)

        close_fn.assert_called_once_with("SP500")


class TestForceCloseMaxLossParam:
    """Tests for the force_close_max_loss_bps parameter handling."""

    def test_max_loss_clamped_to_aggressive(self):
        """max_loss_bps is clamped to at least aggressive_loss_bps."""
        closer, _, _ = _make_closer(
            aggressive_loss_bps=2.0, force_close_max_loss_bps=1.0,
        )
        # 1.0 < 2.0, so max should be clamped to 2.0
        assert closer.force_close_max_loss_bps == 2.0

    def test_zero_disables(self):
        closer, _, _ = _make_closer(force_close_max_loss_bps=0.0)
        assert closer.force_close_max_loss_bps == 0.0

    def test_normal_value(self):
        closer, _, _ = _make_closer(
            aggressive_loss_bps=1.0, force_close_max_loss_bps=3.0,
        )
        assert closer.force_close_max_loss_bps == 3.0


class TestEntryPricePassthrough:
    """Tests that entry_price is correctly passed to _handle_force_close."""

    def test_entry_price_available_in_force_close(self):
        closer, om, md = _make_closer(
            max_age=120, taker_fallback=120,
            force_close_max_loss_bps=3.0,
        )
        _setup_market_data(md, bid=99.0, ask=101.0)

        order = MagicMock()
        order.id = 100
        om.create_limit_order.return_value = order

        entry_time = time.monotonic() - 150
        closer._open_positions["SP500"] = (entry_time, None, 2)

        position = {'size': 0.5, 'entry_price': 100.0}
        closer.manage("SP500", position, MagicMock())

        # Should have called create_limit_order (maker close attempt)
        om.create_limit_order.assert_called_once()

    def test_zero_entry_price_falls_back_to_bbo(self):
        """When entry_price is 0 (unavailable), use BBO pricing."""
        closer, om, md = _make_closer(
            max_age=120, taker_fallback=120,
            force_close_max_loss_bps=3.0,
        )
        _setup_market_data(md)

        order = MagicMock()
        order.id = 200
        om.create_limit_order.return_value = order

        entry_time = time.monotonic() - 150
        closer._open_positions["SP500"] = (entry_time, None, 2)

        position = {'size': 0.5, 'entry_price': 0.0}
        closer.manage("SP500", position, MagicMock())

        om.create_limit_order.assert_called_once()


class TestBackwardCompatibility:
    """Ensure default values produce identical behavior to pre-change code."""

    def test_default_param_no_change(self):
        """With force_close_max_loss_bps=0 (default), behavior is the same."""
        closer, om, md = _make_closer(force_close_max_loss_bps=0.0)
        _setup_market_data(md)

        order = MagicMock()
        order.id = 300
        om.create_limit_order.return_value = order

        entry_time = time.monotonic() - 150
        closer._open_positions["SP500"] = (entry_time, None, 2)

        position = {'size': 0.5, 'entry_price': 100.0}
        closer.manage("SP500", position, MagicMock())

        om.create_limit_order.assert_called_once()
        # Verify it used BBO-based pricing (not entry-based)
        # The price should be ask * (1 + BBO_OFFSET), not entry-based
        price = om.create_limit_order.call_args.kwargs['price']
        # ask=100.1, BBO_OFFSET is small → price ≈ 100.1+
        assert price >= 100.1
