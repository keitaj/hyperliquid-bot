"""Tests for unrealized loss based early taker close.

When a position's unrealized loss exceeds a configurable threshold (in bps),
PositionCloser triggers an immediate taker close instead of waiting for the
age-based force-close.
"""

import time
from unittest.mock import MagicMock

from strategies.mm_position_closer import (
    CLOSE_REASON_UNREALIZED_LOSS,
    PositionCloser,
    _TIER_NORMAL,
    _TIER_BREAKEVEN,
)


def _make_closer(
    max_age: float = 120,
    maker_only: bool = True,
    taker_fallback: float = None,
    spread_bps: float = 10,
    unrealized_loss_close_bps: float = 0.0,
    coin_unrealized_loss_overrides: dict = None,
) -> tuple:
    """Build a PositionCloser with mocked dependencies."""
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
        unrealized_loss_close_bps=unrealized_loss_close_bps,
        coin_unrealized_loss_overrides=coin_unrealized_loss_overrides,
    )
    om.get_all_positions.return_value = [{'coin': 'BTC', 'szi': '1.0'}]
    return closer, om, md


class TestUnrealizedLossLong:
    """Long position: taker close fires when mid_price drops below threshold."""

    def test_long_loss_exceeds_threshold(self):
        """Long: entry=100, mid=99.85 → 15bps loss → close fires."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=15)

        # Set up market data with mid_price causing 15bps loss for long
        market_data_mock = MagicMock()
        market_data_mock.mid_price = 99.85  # (100 - 99.85) / 100 * 10000 = 15 bps
        md.get_market_data.return_value = market_data_mock

        # Register position 10s ago
        entry_time = time.monotonic() - 10
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        position = {'size': 1.0, 'entry_price': 100.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        close_fn.assert_called_once_with('BTC')
        assert 'BTC' not in closer._open_positions
        assert closer.close_stats[CLOSE_REASON_UNREALIZED_LOSS] == 1


class TestUnrealizedLossShort:
    """Short position: taker close fires when mid_price rises above threshold."""

    def test_short_loss_exceeds_threshold(self):
        """Short: entry=100, mid=100.15 → 15bps loss → close fires."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=15)

        market_data_mock = MagicMock()
        market_data_mock.mid_price = 100.15  # (100.15 - 100) / 100 * 10000 = 15 bps
        md.get_market_data.return_value = market_data_mock

        entry_time = time.monotonic() - 10
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        position = {'size': -1.0, 'entry_price': 100.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        close_fn.assert_called_once_with('BTC')
        assert 'BTC' not in closer._open_positions
        assert closer.close_stats[CLOSE_REASON_UNREALIZED_LOSS] == 1


class TestUnrealizedLossBelowThreshold:
    """Loss below threshold: normal flow continues."""

    def test_loss_below_threshold_no_close(self):
        """entry=100, mid=99.95 → 5bps loss → no early close."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=15)

        market_data_mock = MagicMock()
        market_data_mock.mid_price = 99.95  # 5 bps loss
        market_data_mock.bid = 99.94
        market_data_mock.ask = 99.96
        md.get_market_data.return_value = market_data_mock

        entry_time = time.monotonic() - 10
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        mock_order = MagicMock()
        mock_order.id = 42
        om.create_limit_order.return_value = mock_order

        position = {'size': 1.0, 'entry_price': 100.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        close_fn.assert_not_called()
        assert 'BTC' in closer._open_positions


class TestUnrealizedProfit:
    """Unrealized profit: no early close."""

    def test_long_profit_no_close(self):
        """Long: entry=100, mid=100.10 → profit → no close."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=15)

        market_data_mock = MagicMock()
        market_data_mock.mid_price = 100.10  # profit for long
        market_data_mock.bid = 100.09
        market_data_mock.ask = 100.11
        md.get_market_data.return_value = market_data_mock

        entry_time = time.monotonic() - 10
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        mock_order = MagicMock()
        mock_order.id = 42
        om.create_limit_order.return_value = mock_order

        position = {'size': 1.0, 'entry_price': 100.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        close_fn.assert_not_called()
        assert 'BTC' in closer._open_positions


class TestDisabledThreshold:
    """Threshold=0: feature disabled, old behavior preserved."""

    def test_zero_threshold_skips_check(self):
        """threshold=0 → unrealized loss check skipped entirely."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=0)

        # Large loss that would trigger if threshold were non-zero
        market_data_mock = MagicMock()
        market_data_mock.mid_price = 99.0  # 100 bps loss
        market_data_mock.bid = 98.99
        market_data_mock.ask = 99.01
        md.get_market_data.return_value = market_data_mock

        entry_time = time.monotonic() - 10
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        mock_order = MagicMock()
        mock_order.id = 42
        om.create_limit_order.return_value = mock_order

        position = {'size': 1.0, 'entry_price': 100.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        close_fn.assert_not_called()
        assert CLOSE_REASON_UNREALIZED_LOSS not in closer.close_stats


class TestMidPriceUnavailable:
    """mid_price unavailable: skip unrealized loss check."""

    def test_no_market_data_skips_check(self):
        """market_data returns None → existing logic handles position."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=15)

        md.get_market_data.return_value = None

        entry_time = time.monotonic() - 10
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        # Since no market data, take-profit placement also gets None
        position = {'size': 1.0, 'entry_price': 100.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        close_fn.assert_not_called()
        assert CLOSE_REASON_UNREALIZED_LOSS not in closer.close_stats

    def test_zero_mid_price_skips_check(self):
        """mid_price=0 → skip unrealized loss check."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=15)

        market_data_mock = MagicMock()
        market_data_mock.mid_price = 0
        market_data_mock.bid = 0
        market_data_mock.ask = 0
        md.get_market_data.return_value = market_data_mock

        entry_time = time.monotonic() - 10
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        position = {'size': 1.0, 'entry_price': 100.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        close_fn.assert_not_called()
        assert CLOSE_REASON_UNREALIZED_LOSS not in closer.close_stats


class TestCloseReasonLog:
    """Verify close reason is recorded as 'unrealized_loss'."""

    def test_close_reason_logged(self):
        closer, om, md = _make_closer(unrealized_loss_close_bps=10)

        market_data_mock = MagicMock()
        market_data_mock.mid_price = 99.89  # ~11 bps loss
        md.get_market_data.return_value = market_data_mock

        entry_time = time.monotonic() - 30
        closer._open_positions['BTC'] = (entry_time, None, _TIER_BREAKEVEN)

        position = {'size': 1.0, 'entry_price': 100.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        assert closer.close_stats == {CLOSE_REASON_UNREALIZED_LOSS: 1}
        assert closer._close_stats_by_coin['BTC'][CLOSE_REASON_UNREALIZED_LOSS] == 1


class TestCancelExistingCloseOrder:
    """Existing close order is cancelled before taker close."""

    def test_cancel_then_taker_close(self):
        """When a close order exists, cancel it before taker close."""
        closer, om, md = _make_closer(unrealized_loss_close_bps=15)

        market_data_mock = MagicMock()
        market_data_mock.mid_price = 99.84  # 16 bps loss
        md.get_market_data.return_value = market_data_mock

        entry_time = time.monotonic() - 50
        close_oid = 42
        closer._open_positions['BTC'] = (entry_time, close_oid, _TIER_BREAKEVEN)

        position = {'size': 1.0, 'entry_price': 100.0}
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        # Existing close order should be cancelled
        om.cancel_order.assert_called_once_with(close_oid, 'BTC')
        # Then taker close
        close_fn.assert_called_once_with('BTC')
        assert 'BTC' not in closer._open_positions
        assert closer.close_stats[CLOSE_REASON_UNREALIZED_LOSS] == 1


class TestCoinUnrealizedLossOverrides:
    """Per-coin overrides relax (raise) or tighten (lower) the threshold."""

    @staticmethod
    def _setup_position(closer, om, md, mid_price: float, coin: str = 'BTC',
                        size: float = 1.0, entry_price: float = 100.0):
        """Register a position and prime market data for one manage() call."""
        market_data_mock = MagicMock()
        market_data_mock.mid_price = mid_price
        market_data_mock.bid = mid_price - 0.01
        market_data_mock.ask = mid_price + 0.01
        md.get_market_data.return_value = market_data_mock
        om.get_all_positions.return_value = [{'coin': coin, 'szi': str(size)}]
        entry_time = time.monotonic() - 10
        closer._open_positions[coin] = (entry_time, None, _TIER_NORMAL)
        return {'size': size, 'entry_price': entry_price}

    def test_override_relaxes_threshold_blocks_close(self):
        """Override 25bps on BTC: a 20bps loss must NOT trigger early close
        even though the global threshold (15bps) would have."""
        closer, om, md = _make_closer(
            unrealized_loss_close_bps=15,
            coin_unrealized_loss_overrides={'BTC': 25.0},
        )
        # Long: entry=100, mid=99.80 → 20 bps loss (above global 15, below override 25)
        position = self._setup_position(closer, om, md, mid_price=99.80)
        # Provide a place_take_profit success so manage() can complete the cycle
        mock_order = MagicMock()
        mock_order.id = 42
        om.create_limit_order.return_value = mock_order

        close_fn = MagicMock()
        closer.manage('BTC', position, close_fn)

        close_fn.assert_not_called()
        assert closer.close_stats.get(CLOSE_REASON_UNREALIZED_LOSS, 0) == 0
        assert 'BTC' in closer._open_positions

    def test_override_relaxes_threshold_still_fires_above_override(self):
        """Override 25bps on BTC: a 26bps loss DOES trigger close."""
        closer, om, md = _make_closer(
            unrealized_loss_close_bps=15,
            coin_unrealized_loss_overrides={'BTC': 25.0},
        )
        position = self._setup_position(closer, om, md, mid_price=99.74)  # 26 bps
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        close_fn.assert_called_once_with('BTC')
        assert closer.close_stats[CLOSE_REASON_UNREALIZED_LOSS] == 1

    def test_override_tightens_threshold_fires_earlier(self):
        """Override 10bps on BTC: a 12bps loss triggers (global 15 would not)."""
        closer, om, md = _make_closer(
            unrealized_loss_close_bps=15,
            coin_unrealized_loss_overrides={'BTC': 10.0},
        )
        position = self._setup_position(closer, om, md, mid_price=99.88)  # 12 bps
        close_fn = MagicMock()

        closer.manage('BTC', position, close_fn)

        close_fn.assert_called_once_with('BTC')
        assert closer.close_stats[CLOSE_REASON_UNREALIZED_LOSS] == 1

    def test_unspecified_coin_uses_global_threshold(self):
        """Override on BTC only: ETH still uses the global threshold."""
        closer, om, md = _make_closer(
            unrealized_loss_close_bps=15,
            coin_unrealized_loss_overrides={'BTC': 25.0},
        )
        # ETH at 16 bps loss (above global 15) → fires under global rule.
        position = self._setup_position(closer, om, md, mid_price=99.84, coin='ETH')
        close_fn = MagicMock()

        closer.manage('ETH', position, close_fn)

        close_fn.assert_called_once_with('ETH')
        assert closer.close_stats[CLOSE_REASON_UNREALIZED_LOSS] == 1

    def test_dex_prefixed_lookup_falls_back_to_bare(self):
        """Override registered as bare 'NVDA' should also match 'xyz:NVDA'."""
        closer, om, md = _make_closer(
            unrealized_loss_close_bps=15,
            coin_unrealized_loss_overrides={'NVDA': 25.0},
        )
        # 20 bps loss → would fire under global 15, must NOT fire under
        # the bare-name override resolved from xyz:NVDA.
        position = self._setup_position(closer, om, md, mid_price=99.80, coin='xyz:NVDA')
        mock_order = MagicMock()
        mock_order.id = 42
        om.create_limit_order.return_value = mock_order

        close_fn = MagicMock()
        closer.manage('xyz:NVDA', position, close_fn)

        close_fn.assert_not_called()

    def test_override_zero_disables_unrealized_loss_for_coin(self):
        """Setting an override to 0 disables the feature for that coin even if
        the global threshold is non-zero."""
        closer, om, md = _make_closer(
            unrealized_loss_close_bps=15,
            coin_unrealized_loss_overrides={'BTC': 0.0},
        )
        # Way past global 15 bps, but BTC override is 0 → disabled.
        position = self._setup_position(closer, om, md, mid_price=99.50)  # 50 bps
        mock_order = MagicMock()
        mock_order.id = 42
        om.create_limit_order.return_value = mock_order

        close_fn = MagicMock()
        closer.manage('BTC', position, close_fn)

        close_fn.assert_not_called()
        assert closer.close_stats.get(CLOSE_REASON_UNREALIZED_LOSS, 0) == 0

    def test_log_message_reports_effective_threshold(self):
        """The warning log shows the per-coin threshold actually applied."""
        from unittest.mock import patch
        closer, om, md = _make_closer(
            unrealized_loss_close_bps=15,
            coin_unrealized_loss_overrides={'BTC': 10.0},
        )
        position = self._setup_position(closer, om, md, mid_price=99.85)  # 15 bps
        close_fn = MagicMock()

        with patch('strategies.mm_position_closer.logger') as mock_logger:
            closer.manage('BTC', position, close_fn)
            warns = [str(c) for c in mock_logger.warning.call_args_list]
            unrl_lines = [w for w in warns if 'unrealized loss' in w]
            assert len(unrl_lines) == 1
            # Must mention the effective threshold (10bps) rather than the
            # global (15bps).
            assert 'threshold 10' in unrl_lines[0]
