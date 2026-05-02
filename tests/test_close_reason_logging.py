"""Tests for close reason tracking in PositionCloser.

Verifies that close events are classified and logged with the correct
reason (maker, taker_age, external) and that periodic summary logging works.
"""

import time
from unittest.mock import MagicMock, patch

from strategies.mm_position_closer import (
    CLOSE_REASON_EXTERNAL,
    CLOSE_REASON_MAKER,
    CLOSE_REASON_TAKER_AGE,
    PositionCloser,
    _TIER_AGGRESSIVE,
    _TIER_BREAKEVEN,
    _TIER_NORMAL,
)


def _make_closer(max_age=120, maker_only=True, taker_fallback=None, spread_bps=10):
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
    om.get_all_positions.return_value = [{'coin': 'BTC', 'szi': '1.0'}]
    return closer, om, md


class TestCloseReasonRecording:
    """_record_close tracks reason and coin-level stats."""

    def test_record_close_increments_stats(self):
        closer, _, _ = _make_closer()
        closer._record_close("BTC", CLOSE_REASON_MAKER, 60.0, _TIER_NORMAL)
        closer._record_close("BTC", CLOSE_REASON_MAKER, 80.0, _TIER_BREAKEVEN)
        closer._record_close("BTC", CLOSE_REASON_TAKER_AGE, 130.0, _TIER_AGGRESSIVE)

        assert closer.close_stats == {CLOSE_REASON_MAKER: 2, CLOSE_REASON_TAKER_AGE: 1}

    def test_record_close_tracks_per_coin(self):
        closer, _, _ = _make_closer()
        closer._record_close("BTC", CLOSE_REASON_MAKER, 60.0, _TIER_NORMAL)
        closer._record_close("ETH", CLOSE_REASON_TAKER_AGE, 130.0, _TIER_AGGRESSIVE)

        assert closer._close_stats_by_coin["BTC"][CLOSE_REASON_MAKER] == 1
        assert closer._close_stats_by_coin["ETH"][CLOSE_REASON_TAKER_AGE] == 1


class TestRecordCloseEffectiveMaxAge:
    """_record_close emits the trailing ``max_age=Ns`` field when given an
    effective_max_age, and omits it otherwise. The existing log regex
    ``last_tier=(\\S+)`` must still match the same group either way.
    """

    @staticmethod
    def _capture_logs(closer, **kwargs) -> str:
        with patch('strategies.mm_position_closer.logger') as mock_logger:
            closer._record_close(**kwargs)
            calls = [str(c) for c in mock_logger.info.call_args_list]
            close_calls = [c for c in calls if '[close-reason]' in c]
            return close_calls[0] if close_calls else ""

    def test_max_age_appended_when_provided(self):
        closer, _, _ = _make_closer()
        msg = self._capture_logs(
            closer, coin="BTC", reason=CLOSE_REASON_TAKER_AGE,
            age=235.0, tier=_TIER_AGGRESSIVE, effective_max_age=120.0,
        )
        assert 'reason=taker_age' in msg
        assert 'age=235s' in msg
        assert 'last_tier=aggressive' in msg
        assert 'max_age=120s' in msg

    def test_max_age_omitted_when_none(self):
        closer, _, _ = _make_closer()
        msg = self._capture_logs(
            closer, coin="BTC", reason=CLOSE_REASON_MAKER,
            age=45.0, tier=_TIER_NORMAL, effective_max_age=None,
        )
        assert 'last_tier=normal' in msg
        assert 'max_age=' not in msg

    def test_existing_close_reason_regex_still_matches(self):
        # The /review skill greps with this regex; the trailing max_age=
        # field must NOT break the existing capture groups.
        import re
        closer, _, _ = _make_closer()
        msg = self._capture_logs(
            closer, coin="xyz:TSLA", reason=CLOSE_REASON_TAKER_AGE,
            age=235.0, tier=_TIER_AGGRESSIVE, effective_max_age=90.0,
        )
        m = re.search(
            r"\[close-reason\]\s+(\S+)\s+reason=(\S+)\s+age=(\d+)s\s+last_tier=(\S+)",
            msg,
        )
        assert m is not None
        assert m.group(1) == 'xyz:TSLA'
        assert m.group(2) == 'taker_age'
        assert m.group(3) == '235'
        # group(4) is "aggressive" — \S+ stops at the space before max_age
        assert m.group(4) == 'aggressive'

    def test_dynamic_max_age_visible_when_clamped_low(self):
        # The headline use case: distinguish an age=235s close that
        # happened with effective_max_age=90s (DYNAMIC_AGE_MIN biting)
        # from one with effective_max_age=120s (default).
        closer, _, _ = _make_closer()
        clamped = self._capture_logs(
            closer, coin="xyz:TSLA", reason=CLOSE_REASON_TAKER_AGE,
            age=210.0, tier=_TIER_AGGRESSIVE, effective_max_age=90.0,
        )
        normal = self._capture_logs(
            closer, coin="xyz:TSLA", reason=CLOSE_REASON_TAKER_AGE,
            age=240.0, tier=_TIER_AGGRESSIVE, effective_max_age=120.0,
        )
        assert 'max_age=90s' in clamped
        assert 'max_age=120s' in normal


class TestEffectiveMaxAgePropagation:
    """The effective_max_age recorded in the log line should match what
    ``manage()`` actually used, and propagate to externally-driven close
    paths (cleanup_closed, on_position_closed) via _last_effective_max_age.
    """

    def test_manage_records_last_effective_max_age(self):
        # max_age_override=90 should be remembered for subsequent
        # cleanup_closed / on_position_closed that don't see it directly.
        closer, om, _ = _make_closer(max_age=120, taker_fallback=120)
        position = {'size': 1.0, 'entry_price': 100.0}

        # The position must already be tracked, so simulate a prior call
        # by registering it manually.
        closer._open_positions['BTC'] = (time.monotonic() - 5, None, _TIER_NORMAL)

        # This call sets _last_effective_max_age and then proceeds to
        # try placing a close order; the tracked value is what we check.
        closer.manage('BTC', position, lambda c: None, max_age_override=90.0)
        assert closer._last_effective_max_age.get('BTC') == 90.0

    def test_cleanup_closed_uses_last_effective_max_age(self):
        closer, _, _ = _make_closer()
        closer._open_positions['BTC'] = (time.monotonic() - 30, 42, _TIER_BREAKEVEN)
        closer._last_effective_max_age['BTC'] = 90.0

        with patch('strategies.mm_position_closer.logger') as mock_logger:
            closer.cleanup_closed('BTC')
            calls = [str(c) for c in mock_logger.info.call_args_list]
            assert any('max_age=90s' in c for c in calls)
        # Cleanup also evicts the snapshot so a re-opened position starts fresh.
        assert 'BTC' not in closer._last_effective_max_age


class TestCleanupClosedReason:
    """cleanup_closed records maker or external close reason."""

    def test_cleanup_with_close_order_records_maker(self):
        closer, om, _ = _make_closer()
        entry_time = time.monotonic() - 50
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_BREAKEVEN)

        closer.cleanup_closed('BTC')

        assert closer.close_stats[CLOSE_REASON_MAKER] == 1
        assert 'BTC' not in closer._open_positions

    def test_cleanup_without_close_order_records_external(self):
        closer, _, _ = _make_closer()
        entry_time = time.monotonic() - 30
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        closer.cleanup_closed('BTC')

        assert closer.close_stats[CLOSE_REASON_EXTERNAL] == 1

    def test_cleanup_untracked_coin_does_nothing(self):
        closer, _, _ = _make_closer()
        closer.cleanup_closed('BTC')
        assert closer.close_stats == {}


class TestOnPositionClosedReason:
    """on_position_closed records correct close reason."""

    def test_with_close_order_records_maker(self):
        closer, _, _ = _make_closer()
        entry_time = time.monotonic() - 40
        closer._open_positions['BTC'] = (entry_time, 99, _TIER_NORMAL)

        closer.on_position_closed('BTC')

        assert closer.close_stats[CLOSE_REASON_MAKER] == 1

    def test_without_close_order_records_external(self):
        closer, _, _ = _make_closer()
        entry_time = time.monotonic() - 20
        closer._open_positions['BTC'] = (entry_time, None, _TIER_NORMAL)

        closer.on_position_closed('BTC')

        assert closer.close_stats[CLOSE_REASON_EXTERNAL] == 1

    def test_untracked_coin_no_stats(self):
        closer, _, _ = _make_closer()
        closer.on_position_closed('BTC')
        assert closer.close_stats == {}


class TestForceCloseReason:
    """_handle_force_close records taker_age reason."""

    def test_taker_force_close_records_reason(self):
        closer, om, _ = _make_closer(max_age=60, maker_only=False)
        entry_time = time.monotonic() - 120
        closer._open_positions['BTC'] = (entry_time, None, _TIER_AGGRESSIVE)

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 50000.0}

        closer.manage('BTC', position, close_fn)

        close_fn.assert_called_once_with('BTC')
        assert closer.close_stats[CLOSE_REASON_TAKER_AGE] == 1

    def test_taker_force_close_log_includes_tier(self):
        """Force close warning log should include last_tier info."""
        closer, om, _ = _make_closer(max_age=60, maker_only=False)
        entry_time = time.monotonic() - 120
        closer._open_positions['BTC'] = (entry_time, 42, _TIER_AGGRESSIVE)

        close_fn = MagicMock()
        position = {'size': 0.5, 'entry_price': 50000.0}

        with patch("strategies.mm_position_closer.logger") as mock_logger:
            closer.manage('BTC', position, close_fn)
            # Check that the warning log includes tier info
            warning_calls = [
                call for call in mock_logger.warning.call_args_list
                if "force closing" in str(call)
            ]
            assert len(warning_calls) == 1
            log_msg = str(warning_calls[0])
            assert "last_tier=aggressive" in log_msg
            assert "had_close_order=True" in log_msg

    def test_maker_only_force_close_no_taker_reason(self):
        """Maker-only force close should NOT record taker_age."""
        closer, om, md = _make_closer(max_age=60, maker_only=True)
        entry_time = time.monotonic() - 120
        closer._open_positions['BTC'] = (entry_time, None, _TIER_AGGRESSIVE)

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

        # Should not have used taker
        close_fn.assert_not_called()
        # Should not have recorded any close reason (still open, maker close pending)
        assert CLOSE_REASON_TAKER_AGE not in closer.close_stats


class TestLogCloseStats:
    """log_close_stats outputs summary and resets counters."""

    def test_logs_summary_and_resets(self):
        closer, _, _ = _make_closer()
        closer._last_close_stats_log = time.monotonic() - 301  # force immediate log

        closer._record_close("BTC", CLOSE_REASON_MAKER, 50.0, _TIER_NORMAL)
        closer._record_close("BTC", CLOSE_REASON_TAKER_AGE, 130.0, _TIER_AGGRESSIVE)
        closer._record_close("ETH", CLOSE_REASON_TAKER_AGE, 140.0, _TIER_AGGRESSIVE)

        with patch("strategies.mm_position_closer.logger") as mock_logger:
            closer.log_close_stats()
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            summary_calls = [c for c in info_calls if "[close-reason] Summary" in c]
            assert len(summary_calls) == 1
            assert "total=3" in summary_calls[0]

            # Per-coin taker breakdown should be logged
            taker_calls = [c for c in info_calls if "Taker closes by coin" in c]
            assert len(taker_calls) == 1
            assert "BTC=" in taker_calls[0]
            assert "ETH=" in taker_calls[0]

        # Counters should be reset
        assert closer.close_stats == {}

    def test_skips_when_no_data(self):
        closer, _, _ = _make_closer()
        closer._last_close_stats_log = 0.0

        with patch("strategies.mm_position_closer.logger") as mock_logger:
            closer.log_close_stats()
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            summary_calls = [c for c in info_calls if "[close-reason] Summary" in c]
            assert len(summary_calls) == 0

    def test_respects_interval(self):
        closer, _, _ = _make_closer()
        closer._record_close("BTC", CLOSE_REASON_MAKER, 50.0, _TIER_NORMAL)
        # Last log was just now — should skip
        closer._last_close_stats_log = time.monotonic()

        with patch("strategies.mm_position_closer.logger") as mock_logger:
            closer.log_close_stats()
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            summary_calls = [c for c in info_calls if "[close-reason] Summary" in c]
            assert len(summary_calls) == 0

    def test_no_taker_line_when_only_maker(self):
        closer, _, _ = _make_closer()
        closer._last_close_stats_log = 0.0
        closer._record_close("BTC", CLOSE_REASON_MAKER, 50.0, _TIER_NORMAL)

        with patch("strategies.mm_position_closer.logger") as mock_logger:
            closer.log_close_stats()
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            taker_calls = [c for c in info_calls if "Taker closes by coin" in c]
            assert len(taker_calls) == 0
