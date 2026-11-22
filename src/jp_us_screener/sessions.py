"""Session cut for the saved morning run. This module does not schedule anything.

automation-1795319781957 already starts the run. There is no cron here.
The cut is 08:00 Asia/Hong_Kong on the run's Hong Kong date. Hong Kong is
UTC+8 all year, so the cut does not move when the US is on daylight saving time.
A start before or after 08:00 on that same Hong Kong date still uses 08:00,
which keeps the same-day Japan cash close (14:00 Hong Kong) out of the run.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

HKT = ZoneInfo("Asia/Hong_Kong")
NEW_YORK = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")

ROUTINE_ID = "automation-1795319781957"
WINDOW_START = time(7, 45)
WINDOW_END = time(8, 15)
RUN_WEEKDAYS = frozenset(range(6))  # Monday through Saturday

US_CLOSE = time(16, 0)
JP_CLOSE = time(15, 0)


def as_hkt(now: datetime) -> datetime:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("時間必須帶時區")
    return now.astimezone(HKT)


def session_cut(now: datetime) -> datetime:
    """08:00 Hong Kong on the same Hong Kong calendar date as `now`."""

    local = as_hkt(now)
    return local.replace(hour=8, minute=0, second=0, microsecond=0)


def within_start_window(now: datetime) -> bool:
    """True when `now` is Mon–Sat 07:45–08:15 Hong Kong. Informational only."""

    local = as_hkt(now)
    if local.weekday() not in RUN_WEEKDAYS:
        return False
    clock = local.time()
    return WINDOW_START <= clock <= WINDOW_END


def _last_closed(moment: datetime, zone: ZoneInfo, close: time) -> date:
    cursor = moment.astimezone(zone).date()
    for _ in range(14):
        if cursor.weekday() < 5:
            closed = datetime(cursor.year, cursor.month, cursor.day, close.hour, close.minute, tzinfo=zone)
            if closed <= moment:
                return cursor
        cursor -= timedelta(days=1)
    raise RuntimeError("找不到已收市的交易日")


def last_completed_us_session(moment: datetime) -> date:
    return _last_closed(moment, NEW_YORK, US_CLOSE)


def last_completed_jp_session(moment: datetime) -> date:
    return _last_closed(moment, TOKYO, JP_CLOSE)


def expected_sessions(moment: datetime) -> dict[str, date]:
    return {"US": last_completed_us_session(moment), "JP": last_completed_jp_session(moment)}
