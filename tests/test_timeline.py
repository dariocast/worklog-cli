from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from worklog_cli.timeline import Raw, per_day, spans, total_seconds

IDLE = timedelta(minutes=15)


def t(hour: int, minute: int = 0, day: int = 30) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def turn(task: str, start: datetime, end: datetime) -> Raw:
    return Raw(task, start, end, manual=False)


def test_short_gaps_join_long_gaps_split():
    raws = [
        turn("A", t(9, 0), t(9, 4)),
        turn("A", t(9, 10), t(9, 12)),
        turn("A", t(9, 20), t(9, 35)),
        turn("A", t(14, 0), t(14, 6)),
    ]
    result = spans(raws, IDLE)
    assert [(s.start, s.end) for s in result] == [(t(9), t(9, 35)), (t(14), t(14, 6))]
    assert total_seconds(result) == 41 * 60


def test_gap_equal_to_threshold_is_not_counted():
    result = spans([turn("A", t(9), t(9, 5)), turn("A", t(9, 20), t(9, 25))], IDLE)
    assert total_seconds(result) == 10 * 60


def test_overlapping_chats_on_same_task_count_once():
    result = spans([turn("A", t(10), t(11)), turn("A", t(10, 30), t(10, 50))], IDLE)
    assert total_seconds(result) == 3600


def test_parallel_tasks_count_in_full():
    result = spans([turn("A", t(10), t(11)), turn("B", t(10), t(11))], IDLE)
    assert total_seconds(result) == 2 * 3600


def test_manual_entries_are_counted_as_entered():
    raws = [
        Raw("A", t(9), t(10), manual=True),
        Raw("A", t(9), t(9, 30), manual=True),
        turn("A", t(9, 45), t(9, 50)),
    ]
    assert total_seconds(spans(raws, IDLE)) == (60 + 30 + 5) * 60


def test_zero_threshold_still_merges_overlaps():
    result = spans([turn("A", t(9), t(9, 10)), turn("A", t(9, 5), t(9, 20))], timedelta(0))
    assert total_seconds(result) == 20 * 60


def test_days_split_at_local_midnight():
    rome = ZoneInfo("Europe/Rome")
    result = spans([Raw("A", t(21, 30), t(23, 0), manual=True)], IDLE)
    totals = per_day(result, rome)
    assert totals == {(date(2026, 9, 30), "A"): 30 * 60, (date(2026, 10, 1), "A"): 3600}


def test_daylight_saving_day_has_25_hours():
    rome = ZoneInfo("Europe/Rome")
    start = datetime(2026, 10, 24, 22, tzinfo=UTC)  # 00:00 local on the 25th
    result = spans([Raw("A", start, start + timedelta(hours=26), manual=True)], IDLE)
    totals = per_day(result, rome)
    assert totals[(date(2026, 10, 25), "A")] == 25 * 3600
    assert totals[(date(2026, 10, 26), "A")] == 3600
