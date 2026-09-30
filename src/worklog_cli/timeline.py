"""Derive worked time from raw intervals. Pure functions, no storage.

Agent turns of the same task are joined when the idle gap between them is
shorter than the threshold, and merged when they overlap (two chats on one
task count once). Manual entries are counted exactly as entered. Different
tasks never affect each other: parallel work counts in full for each task.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

from worklog_cli.timeutil import day_start, to_local


@dataclass(frozen=True)
class Raw:
    task_id: str
    start: datetime
    end: datetime
    manual: bool
    note: str | None = None


@dataclass(frozen=True)
class Span:
    task_id: str
    start: datetime
    end: datetime

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def spans(raws: Iterable[Raw], idle_threshold: timedelta) -> list[Span]:
    """Worked spans for all tasks, sorted by task then start."""
    by_task: dict[str, list[Raw]] = {}
    for raw in raws:
        by_task.setdefault(raw.task_id, []).append(raw)
    result: list[Span] = []
    for task_id in sorted(by_task):
        items = by_task[task_id]
        joined: list[Span] = []
        for raw in sorted((r for r in items if not r.manual), key=lambda r: r.start):
            last = joined[-1] if joined else None
            if last is not None and (
                raw.start - last.end < idle_threshold or raw.start <= last.end
            ):
                joined[-1] = Span(task_id, last.start, max(last.end, raw.end))
            else:
                joined.append(Span(task_id, raw.start, raw.end))
        joined.extend(Span(task_id, r.start, r.end) for r in items if r.manual)
        result.extend(sorted(joined, key=lambda s: s.start))
    return result


def per_day(items: Iterable[Span], tz: tzinfo | None) -> dict[tuple[date, str], float]:
    """Seconds per (local day, task), splitting spans at local midnight."""
    totals: dict[tuple[date, str], float] = {}
    for span in items:
        cursor = span.start
        while cursor < span.end:
            day = to_local(cursor, tz).date()
            boundary = day_start(day + timedelta(days=1), tz)
            stop = min(span.end, boundary)
            key = (day, span.task_id)
            totals[key] = totals.get(key, 0.0) + (stop - cursor).total_seconds()
            cursor = stop
    return totals


def total_seconds(items: Iterable[Span]) -> float:
    return sum(span.seconds for span in items)
