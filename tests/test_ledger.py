import sqlite3

import pytest
from conftest import at

from worklog_cli import spool
from worklog_cli.errors import WorklogError
from worklog_cli.ledger import Ledger
from worklog_cli.reporting import report


def prompt(db, cwd, when, session="s1", agent="claude"):
    return db.apply_event(
        {"event": "prompt", "agent": agent, "session_id": session, "cwd": str(cwd), "ts": when}
    )


def activity(db, when, session="s1", agent="claude"):
    db.apply_event({"event": "activity", "agent": agent, "session_id": session, "ts": when})


def ts(text):
    return at(text).isoformat()


def only_task(db):
    tasks = db.tasks()
    assert len(tasks) == 1
    return tasks[0]


def test_turns_become_worked_time_with_idle_joining(ledger, tmp_path):
    work = tmp_path / "work" / "repo"
    context = prompt(ledger, work, ts("09:00"))
    assert "[WorkLog]" in context and "claude:s1" in context
    activity(ledger, ts("09:04"))
    assert prompt(ledger, work, ts("09:10")) is None  # context unchanged: not repeated
    activity(ledger, ts("09:12"))
    prompt(ledger, work, ts("14:00"))
    activity(ledger, ts("14:03"))
    activity(ledger, ts("14:06"))
    task = only_task(ledger)
    assert task["project"] == "example-project"
    assert task["provisional_title"] and "repo" in task["title"]
    assert task["duration_seconds"] == (12 + 6) * 60
    assert len(ledger.show(task["id"])["intervals"]) == 3


def test_interrupted_turn_ends_at_last_activity(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    activity(ledger, ts("09:40"))
    prompt(ledger, tmp_path / "work", ts("11:00"))  # no activity at all: counts zero
    assert only_task(ledger)["duration_seconds"] == 40 * 60


def test_activity_never_extends_other_sessions_or_earlier_turns(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"), session="a")
    prompt(ledger, tmp_path / "work", ts("09:00"), session="b")
    activity(ledger, ts("09:30"), session="a")
    activity(ledger, ts("08:00"), session="b")  # before any turn of b: ignored
    durations = sorted(t["duration_seconds"] for t in ledger.tasks())
    assert durations == [0, 30 * 60]


def test_ignored_directory_is_never_tracked(ledger, tmp_path):
    assert prompt(ledger, tmp_path / "own" / "notes", ts("09:00")) is None
    activity(ledger, ts("09:30"))
    assert ledger.tasks() == []


def test_unmapped_directory_goes_to_inbox_and_map_moves_it(ledger, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "repo"
    context = prompt(ledger, elsewhere, ts("09:00"))
    assert "inbox" in context and "worklog map" in context
    activity(ledger, ts("09:20"))
    assert [i["cwd"] for i in ledger.inbox()] == [str(elsewhere)]
    assert ledger.remap(str(tmp_path / "elsewhere"), "example-client") == {"moved_sessions": 1}
    assert ledger.inbox() == []
    assert only_task(ledger)["project"] == "example-client"
    assert "example-client" in prompt(ledger, elsewhere, ts("09:30"))  # re-injected


def test_map_to_ignore_discards_inbox_time(ledger, tmp_path):
    prompt(ledger, tmp_path / "elsewhere", ts("09:00"))
    activity(ledger, ts("09:20"))
    ledger.remap(str(tmp_path / "elsewhere"), "ignore")
    assert ledger.tasks() == []
    assert prompt(ledger, tmp_path / "elsewhere", ts("09:30")) is None


def test_two_chats_on_one_task_count_overlap_once(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"), session="plan")
    activity(ledger, ts("10:00"), session="plan")
    ledger.assign("claude:plan", title="Crash on startup", ref="DEMO-123")
    task_id = ledger.session_info("claude", "plan")["task_id"]
    prompt(ledger, tmp_path / "work", ts("09:30"), session="build", agent="codex")
    activity(ledger, ts("10:30"), session="build", agent="codex")
    info = ledger.assign("codex:build", task_id=task_id)
    assert info["task_id"] == task_id and info["ref"] == "DEMO-123"
    task = only_task(ledger)  # the provisional task of the second chat was dropped
    assert task["duration_seconds"] == 90 * 60
    assert task["title"] == "Crash on startup" and not task["provisional_title"]
    assert ledger.show(task_id)["sessions"] == ["claude:plan", "codex:build"]


def test_ignore_session_discards_time_and_stops_tracking(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    activity(ledger, ts("09:30"))
    assert ledger.ignore("claude:s1") == {"session": "claude:s1", "removed_intervals": 1}
    assert prompt(ledger, tmp_path / "work", ts("10:00")) is None
    assert ledger.tasks() == []
    with pytest.raises(WorklogError):
        ledger.assign("claude:s1", title="x")


def test_manual_log_move_and_remove(ledger, clock):
    ledger.add_project("example-project")
    logged = ledger.log(at("09:00"), at("10:30"), project="example-project", title="Meeting")
    other = ledger.add_task("example-project", "Planning")
    moved = ledger.move(logged["id"][:8], other["id"])
    assert moved["task_id"] == other["id"]
    assert [t["title"] for t in ledger.tasks()] == ["Meeting", "Planning"]  # manual task kept
    removed = ledger.remove(logged["id"][:6])
    assert removed["duration_seconds"] == 5400
    assert ledger.show(other["id"])["intervals"] == []


@pytest.mark.parametrize(
    "start,end,message",
    [("10:00", "09:00", "after its start"), ("17:00", "19:00", "future")],
)
def test_log_rejects_bad_intervals(ledger, start, end, message):
    ledger.add_project("example-project")
    task = ledger.add_task("example-project", "Meeting")
    with pytest.raises(WorklogError, match=message):
        ledger.log(at(start), at(end), task_id=task["id"])
    assert ledger.show(task["id"])["intervals"] == []


def test_log_requires_existing_project_and_rejects_reserved(ledger):
    with pytest.raises(WorklogError, match="Unknown project"):
        ledger.log(at("09:00"), at("10:00"), project="typo", title="x")
    with pytest.raises(WorklogError, match="reserved"):
        ledger.add_project("inbox")


def test_report_rows_per_day_and_task_with_notes_and_rounding(ledger, tmp_path):
    ledger.add_project("example-project")
    task = ledger.add_task("example-project", "Review", ref="DEMO-7")
    ledger.log(at("09:00"), at("09:52"), task_id=task["id"], note="First pass")
    ledger.log(at("11:00"), at("11:20"), task_id=task["id"], note="First pass")
    prompt(ledger, tmp_path / "work", ts("15:00"))
    activity(ledger, ts("15:07"))
    day = at("09:00").date()
    data = report(ledger, day, day, None, 15 * 60)
    rows = {r["key"]: r for r in data["entries"]}
    assert rows["DEMO-7"]["duration_seconds"] == 75 * 60
    assert rows["DEMO-7"]["raw_seconds"] == 72 * 60
    assert rows["DEMO-7"]["notes"] == ["First pass"]
    assert rows["example-project"]["duration_seconds"] == 0  # 7 minutes round to zero
    assert data["duration_seconds"] == 75 * 60


def test_spool_replay_and_invalid_events(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    spool.append({"event": "activity", "agent": "claude", "session_id": "s1", "ts": ts("09:25")})
    spool.append({"event": "bogus", "agent": "claude", "session_id": "s1", "ts": ts("09:26")})
    ledger.drain()
    assert spool.pending() == 0
    assert only_task(ledger)["duration_seconds"] == 25 * 60
    assert any("Dropped" in line for line in spool.errors())


def test_spool_keeps_events_when_storage_fails(ledger, tmp_path, monkeypatch):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    spool.append({"event": "activity", "agent": "claude", "session_id": "s1", "ts": ts("09:10")})
    spool.append({"event": "activity", "agent": "claude", "session_id": "s1", "ts": ts("09:20")})

    def broken(event):
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as patched:
        patched.setattr(ledger, "apply_event", broken)
        ledger.drain()
        assert spool.pending() == 2
    ledger.drain()
    assert spool.pending() == 0
    assert only_task(ledger)["duration_seconds"] == 20 * 60


def test_old_ledger_is_rejected(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version = 1")
    connection.close()
    with pytest.raises(WorklogError, match="0.1 ledger"):
        Ledger(path)
