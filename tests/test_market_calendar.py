"""Tests for strategies.market_calendar (session phase resolution)."""

from datetime import datetime, timezone

import pytest

from strategies.market_calendar import (
    MARKETS,
    PHASE_CLOSED,
    PHASE_CLOSE_WINDOW,
    PHASE_LUNCH,
    PHASE_OPEN_WINDOW,
    PHASE_REGULAR,
    VALID_PHASES,
    phase_of,
)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class TestNyse:
    """US cash equities: 09:30-16:00 America/New_York, DST-aware."""

    def test_midday_summer_is_regular(self):
        # 2026-06-10 is a Wednesday; 15:00 UTC = 11:00 EDT.
        assert phase_of("NYSE", _utc(2026, 6, 10, 15, 0)) == PHASE_REGULAR

    def test_open_window_summer(self):
        # 13:45 UTC = 09:45 EDT, 15 minutes after the open.
        assert phase_of("NYSE", _utc(2026, 6, 10, 13, 45)) == PHASE_OPEN_WINDOW

    def test_close_window_summer(self):
        # 19:45 UTC = 15:45 EDT, 15 minutes before the close.
        assert phase_of("NYSE", _utc(2026, 6, 10, 19, 45)) == PHASE_CLOSE_WINDOW

    def test_overnight_closed(self):
        assert phase_of("NYSE", _utc(2026, 6, 10, 3, 0)) == PHASE_CLOSED

    def test_weekend_closed(self):
        # 2026-06-13 is a Saturday.
        assert phase_of("NYSE", _utc(2026, 6, 13, 15, 0)) == PHASE_CLOSED

    def test_dst_shift_moves_utc_open(self):
        # US DST 2026 starts Sunday March 8. 14:00 UTC is 09:00 EST
        # (closed) on Friday March 6 but 10:00 EDT (open) on Monday March 9.
        assert phase_of("NYSE", _utc(2026, 3, 6, 14, 0)) == PHASE_CLOSED
        assert phase_of("NYSE", _utc(2026, 3, 9, 14, 0)) != PHASE_CLOSED

    def test_custom_window_minutes(self):
        # With a 60-minute open window, 10:15 ET is still open_window.
        assert phase_of("NYSE", _utc(2026, 6, 10, 14, 15),
                        open_window_minutes=60) == PHASE_OPEN_WINDOW
        # With windows disabled it is regular.
        assert phase_of("NYSE", _utc(2026, 6, 10, 13, 45),
                        open_window_minutes=0) == PHASE_REGULAR


class TestKrx:
    """Korea: 09:00-15:30 Asia/Seoul, no DST — UTC window is constant."""

    @pytest.mark.parametrize("month", [1, 6])
    def test_midday_regular_year_round(self, month):
        # 02:00 UTC = 11:00 KST; 2026-01-14 and 2026-06-10 are Wednesdays.
        day = 14 if month == 1 else 10
        assert phase_of("KRX", _utc(2026, month, day, 2, 0)) == PHASE_REGULAR

    def test_after_close(self):
        # 07:00 UTC = 16:00 KST.
        assert phase_of("KRX", _utc(2026, 6, 10, 7, 0)) == PHASE_CLOSED


class TestTse:
    """Tokyo: 09:00-11:30 / 12:30-15:30 with a lunch phase."""

    def test_lunch_phase(self):
        # 03:00 UTC = 12:00 JST.
        assert phase_of("TSE", _utc(2026, 6, 10, 3, 0)) == PHASE_LUNCH

    def test_before_lunch_is_regular_not_close_window(self):
        # 02:15 UTC = 11:15 JST — lunch must not create a close window.
        assert phase_of("TSE", _utc(2026, 6, 10, 2, 15)) == PHASE_REGULAR

    def test_after_lunch_is_regular_not_open_window(self):
        # 03:45 UTC = 12:45 JST — lunch must not create an open window.
        assert phase_of("TSE", _utc(2026, 6, 10, 3, 45)) == PHASE_REGULAR


class TestCmeIndex:
    """CME Globex style: Sun 17:00 -> Fri 16:00 CT, daily 16:00-17:00 halt."""

    def test_saturday_closed(self):
        assert phase_of("CME_INDEX", _utc(2026, 6, 13, 12, 0)) == PHASE_CLOSED

    def test_sunday_reopen_window(self):
        # 2026-06-07 is a Sunday; 22:15 UTC = 17:15 CDT, 15 min after reopen.
        assert phase_of("CME_INDEX", _utc(2026, 6, 7, 22, 15)) == PHASE_OPEN_WINDOW

    def test_daily_halt_closed(self):
        # Wednesday 21:30 UTC = 16:30 CDT.
        assert phase_of("CME_INDEX", _utc(2026, 6, 10, 21, 30)) == PHASE_CLOSED

    def test_friday_after_weekly_close(self):
        # Friday 21:30 UTC = 16:30 CDT.
        assert phase_of("CME_INDEX", _utc(2026, 6, 12, 21, 30)) == PHASE_CLOSED

    def test_overnight_weekday_regular(self):
        # Wednesday 08:00 UTC = 03:00 CDT — Globex trades overnight.
        assert phase_of("CME_INDEX", _utc(2026, 6, 10, 8, 0)) == PHASE_REGULAR


class TestFx:
    """FX convention: Sun 17:00 -> Fri 17:00 New York, zero-length halt."""

    def test_no_daily_halt(self):
        # Wednesday 21:00 UTC = 17:00 EDT — zero-length halt never closes.
        assert phase_of("FX", _utc(2026, 6, 10, 21, 0)) == PHASE_REGULAR

    def test_weekend_closed(self):
        assert phase_of("FX", _utc(2026, 6, 13, 12, 0)) == PHASE_CLOSED


class TestMisc:
    def test_none_market_always_regular(self):
        assert phase_of("NONE", _utc(2026, 6, 13, 3, 0)) == PHASE_REGULAR

    def test_naive_datetime_treated_as_utc(self):
        assert phase_of("NYSE", datetime(2026, 6, 10, 15, 0)) == PHASE_REGULAR

    def test_unknown_market_raises(self):
        with pytest.raises(KeyError):
            phase_of("NARNIA", _utc(2026, 6, 10, 15, 0))

    def test_all_market_ids_resolve_some_phase(self):
        for market_id in MARKETS:
            assert phase_of(market_id, _utc(2026, 6, 10, 15, 0)) in VALID_PHASES
