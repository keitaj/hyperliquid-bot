"""Built-in market calendars and session-phase resolution.

Maps a market id (``"NYSE"``, ``"KRX"``, ...) to its underlying cash/futures
session expressed in the market's own wall-clock time zone, and resolves the
current *phase* of that session for an aware UTC timestamp:

``closed`` / ``open_window`` / ``regular`` / ``lunch`` / ``close_window``

Session times are stored as local wall-clock values and converted through
:mod:`zoneinfo`, so daylight-saving transitions are handled by the tz
database and never require a config change.

Phase windows are derived by probing the closed/open predicate shortly
before and after ``now``: the first ``open_window_minutes`` after any
reopen (daily open, post-halt, Sunday reopen for continuous markets) is
``open_window`` and the last ``close_window_minutes`` before any close is
``close_window``. A lunch break is reported as its own ``lunch`` phase and
deliberately does not create open/close windows around itself.

Holidays are out of scope: on an exchange holiday the calendar still
reports the regular weekday session. Detecting *actual* closure is the job
of the oracle staleness guard, which observes the price feed instead of a
calendar.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from typing import FrozenSet, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

PHASE_CLOSED = "closed"
PHASE_OPEN_WINDOW = "open_window"
PHASE_REGULAR = "regular"
PHASE_LUNCH = "lunch"
PHASE_CLOSE_WINDOW = "close_window"

VALID_PHASES: FrozenSet[str] = frozenset({
    PHASE_CLOSED, PHASE_OPEN_WINDOW, PHASE_REGULAR, PHASE_LUNCH, PHASE_CLOSE_WINDOW,
})

_WEEKDAYS_MON_FRI: FrozenSet[int] = frozenset({0, 1, 2, 3, 4})


@dataclass(frozen=True)
class MarketSpec:
    """One market's session description in its local time zone.

    Two shapes are supported:

    * Daily session (``open_t``/``close_t`` set, ``continuous=False``):
      open on ``weekdays`` between the two wall-clock times, with an
      optional lunch break.
    * Continuous week (``continuous=True``): open from Sunday
      ``halt_end`` until Friday ``halt_start`` with a daily maintenance
      halt between ``halt_start`` and ``halt_end`` (zero-length halt
      allowed, e.g. FX).
    """

    tz: str
    weekdays: FrozenSet[int] = _WEEKDAYS_MON_FRI
    open_t: Optional[time] = None
    close_t: Optional[time] = None
    lunch_start: Optional[time] = None
    lunch_end: Optional[time] = None
    continuous: bool = False
    halt_start: Optional[time] = None
    halt_end: Optional[time] = None
    always_open: bool = False


MARKETS = {
    # US cash equities.
    "NYSE": MarketSpec(tz="America/New_York", open_t=time(9, 30), close_t=time(16, 0)),
    # US index futures (CME Globex): Sun 17:00 -> Fri 16:00 CT with a
    # daily 16:00-17:00 CT maintenance halt.
    "CME_INDEX": MarketSpec(tz="America/Chicago", continuous=True,
                            halt_start=time(16, 0), halt_end=time(17, 0)),
    # Korea Exchange, no DST in Asia/Seoul.
    "KRX": MarketSpec(tz="Asia/Seoul", open_t=time(9, 0), close_t=time(15, 30)),
    # Tokyo Stock Exchange with its lunch break.
    "TSE": MarketSpec(tz="Asia/Tokyo", open_t=time(9, 0), close_t=time(15, 30),
                      lunch_start=time(11, 30), lunch_end=time(12, 30)),
    # Hong Kong Exchange with its lunch break.
    "HKEX": MarketSpec(tz="Asia/Hong_Kong", open_t=time(9, 30), close_t=time(16, 0),
                       lunch_start=time(12, 0), lunch_end=time(13, 0)),
    # Spot FX convention: Sun 17:00 -> Fri 17:00 New York, no daily halt.
    "FX": MarketSpec(tz="America/New_York", continuous=True,
                     halt_start=time(17, 0), halt_end=time(17, 0)),
    # Explicit opt-out for assets without an underlying session
    # (pre-IPO perps etc.): always "regular".
    "NONE": MarketSpec(tz="UTC", always_open=True),
}


@lru_cache(maxsize=None)
def _zone(tz: str) -> ZoneInfo:
    return ZoneInfo(tz)


def _ensure_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _is_closed(spec: MarketSpec, utc_dt: datetime) -> bool:
    """True when the underlying market is not trading at ``utc_dt``.

    A lunch break does *not* count as closed here (it is its own phase and
    must not generate open/close windows around itself).
    """
    if spec.always_open:
        return False
    local = utc_dt.astimezone(_zone(spec.tz))
    wd, t = local.weekday(), local.time()
    if spec.continuous:
        if wd == 5:  # Saturday
            return True
        if wd == 4 and t >= spec.halt_start:  # Friday after the weekly close
            return True
        if wd == 6 and t < spec.halt_end:  # Sunday before the weekly reopen
            return True
        if wd <= 3 and spec.halt_start <= t < spec.halt_end:  # daily halt
            return True
        return False
    if wd not in spec.weekdays:
        return True
    return t < spec.open_t or t >= spec.close_t


def _in_lunch(spec: MarketSpec, utc_dt: datetime) -> bool:
    if spec.lunch_start is None or spec.lunch_end is None:
        return False
    local_t = utc_dt.astimezone(_zone(spec.tz)).time()
    return spec.lunch_start <= local_t < spec.lunch_end


def phase_of(market_id: str, now_utc: datetime,
             open_window_minutes: float = 30.0,
             close_window_minutes: float = 30.0) -> str:
    """Resolve the session phase of ``market_id`` at ``now_utc``.

    Raises ``KeyError`` for an unknown market id; callers are expected to
    validate assignments once at startup and fall back to legacy behaviour.
    A naive ``now_utc`` is interpreted as UTC.
    """
    spec = MARKETS[market_id]
    if spec.always_open:
        return PHASE_REGULAR
    now = _ensure_aware_utc(now_utc)
    if _is_closed(spec, now):
        return PHASE_CLOSED
    if _in_lunch(spec, now):
        return PHASE_LUNCH
    if open_window_minutes > 0 and _is_closed(spec, now - timedelta(minutes=open_window_minutes)):
        return PHASE_OPEN_WINDOW
    if close_window_minutes > 0 and _is_closed(spec, now + timedelta(minutes=close_window_minutes)):
        return PHASE_CLOSE_WINDOW
    return PHASE_REGULAR
