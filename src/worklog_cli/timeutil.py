"""Timestamps, durations and local calendar days.

Stored timestamps are aware UTC ISO strings. A ``tz`` of ``None`` means the
system timezone, resolved per instant so daylight-saving changes are honored.
"""

import re
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from worklog_cli.errors import WorklogError

DURATION = re.compile(r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)(?P<unit>m)?)?$")
CLOCK = re.compile(r"^(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?$")


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("naive datetime")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_duration(value: str) -> int:
    """Parse ``1h30``, ``1h30m``, ``90m`` or ``2h`` into seconds."""
    match = DURATION.match(value.strip())
    if not match or not value.strip():
        raise WorklogError(f"Invalid duration {value!r}; use forms like 1h30, 90m or 2h")
    hours, minutes = match["h"], match["m"]
    if hours is None and match["unit"] is None:
        raise WorklogError(f"Invalid duration {value!r}; add a unit, for example {value}m")
    total = int(hours or 0) * 3600 + int(minutes or 0) * 60
    if total <= 0:
        raise WorklogError("Duration must be positive")
    return total


def parse_clock(value: str) -> time:
    match = CLOCK.match(value.strip())
    if not match:
        raise WorklogError(f"Invalid time {value!r}; use HH:MM or HH:MM:SS")
    try:
        return time(int(match["h"]), int(match["m"]), int(match["s"] or 0))
    except ValueError as exc:
        raise WorklogError(f"Invalid time {value!r}: {exc}") from exc


def to_local(moment: datetime, tz: tzinfo | None) -> datetime:
    return moment.astimezone(tz) if tz is not None else moment.astimezone()


def local_moment(day: date, clock: time, tz: tzinfo | None) -> datetime:
    naive = datetime.combine(day, clock)
    return naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()


def day_start(day: date, tz: tzinfo | None) -> datetime:
    return local_moment(day, time(0), tz)


def parse_day(value: str, now: datetime, tz: tzinfo | None) -> date:
    today = to_local(now, tz).date()
    if value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WorklogError(f"Invalid day {value!r}; use YYYY-MM-DD, today or yesterday") from exc


def human_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60}h{minutes % 60:02d}"
