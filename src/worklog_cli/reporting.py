"""Interval-based reports, independent of storage and presentation."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from worklog_cli.errors import WorklogError


def report(
    tasks: list[dict[str, Any]],
    start: str | None,
    end: str | None,
    timezone: str,
    now: datetime,
) -> dict[str, Any]:
    try:
        zone = ZoneInfo(timezone)
        first = date.fromisoformat(start) if start else None
        last = date.fromisoformat(end) if end else None
        if first and last and first > last:
            raise ValueError("--from must be on or before --to")
        lower = datetime.combine(first, time.min, zone).astimezone(UTC) if first else None
        upper = (
            datetime.combine(last + timedelta(days=1), time.min, zone).astimezone(UTC)
            if last
            else None
        )
    except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
        raise WorklogError(f"Invalid report range/timezone: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for task in tasks:
        duration = 0.0
        count = 0
        for session in task["sessions"]:
            a = datetime.fromisoformat(session["started_at"])
            b = datetime.fromisoformat(session["ended_at"]) if session["ended_at"] else now
            a = max(a, lower) if lower else a
            b = min(b, upper) if upper else b
            if b > a:
                duration += (b - a).total_seconds()
                count += 1
        if count:
            rows.append(
                {
                    "task_id": task["id"],
                    "project": task["project"],
                    "title": task["title"],
                    "session_count": count,
                    "duration_seconds": round(duration, 6),
                }
            )
    projects: dict[str, float] = {}
    for row in rows:
        projects[row["project"]] = projects.get(row["project"], 0) + row["duration_seconds"]
    return {
        "from": start,
        "to": end,
        "timezone": timezone,
        "as_of": now.isoformat(),
        "tasks": rows,
        "projects": [
            {"project": p, "duration_seconds": round(d, 6)} for p, d in sorted(projects.items())
        ],
        "duration_seconds": round(sum(projects.values()), 6),
    }
