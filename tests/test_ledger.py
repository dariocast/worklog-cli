import sqlite3

import pytest
from conftest import at

from worklog_cli import spool
from worklog_cli.errors import WorklogError
from worklog_cli.ledger import Ledger
from worklog_cli.reporting import report
from worklog_cli.timeutil import parse_iso


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


def test_remove_empty_task_releases_its_chat(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"))  # chat opened, no activity
    task = only_task(ledger)
    removed = ledger.remove_task(task["id"])
    assert removed["released_sessions"] == ["claude:s1"]
    assert removed["removed_intervals"] == 1
    assert ledger.tasks() == []
    assert "[WorkLog]" in prompt(ledger, tmp_path / "work", ts("10:00"))  # tracked anew
    assert only_task(ledger)["id"] != task["id"]


def test_remove_task_with_time_requires_force(ledger):
    ledger.add_project("example-project")
    logged = ledger.log(at("09:00"), at("10:00"), project="example-project", title="Meeting")
    with pytest.raises(WorklogError, match="--force") as error:
        ledger.remove_task(logged["task_id"])
    assert error.value.code == "conflict"
    assert len(ledger.show(logged["task_id"])["intervals"]) == 1
    assert ledger.remove_task(logged["task_id"], force=True)["duration_seconds"] == 3600
    with pytest.raises(WorklogError, match="Unknown task"):
        ledger.show(logged["task_id"])


def test_capture_goes_to_inbox_on_top_without_time(ledger):
    first = ledger.add_task(None, "Read the proposal")
    ledger.add_project("example-project")
    second = ledger.add_task("example-project", "Check the logs", description=" why ")
    assert first["project"] == "inbox" and not first["started"]
    assert second["description"] == "why" and second["rank"] < first["rank"]
    assert [t["id"] for t in ledger.tasks(started=False)] == [first["id"], second["id"]]
    with pytest.raises(WorklogError, match="reserved"):
        ledger.add_task("ignore", "x")


def test_started_filter_follows_intervals(ledger):
    ledger.add_project("example-project")
    planned = ledger.add_task("example-project", "Planned")
    logged = ledger.log(at("09:00"), at("10:00"), project="example-project", title="Meeting")
    assert [t["id"] for t in ledger.tasks(started=True)] == [logged["task_id"]]
    assert [t["id"] for t in ledger.tasks(started=False)] == [planned["id"]]
    ledger.remove(logged["id"][:8])
    assert ledger.show(logged["task_id"])["started"] is False


def test_edit_project_description_and_status(ledger, clock):
    capture = ledger.add_task(None, "Call back")
    ledger.add_project("example-project")
    edited = ledger.edit(capture["id"], project="example-project", description="Before Friday")
    assert edited["project"] == "example-project" and edited["description"] == "Before Friday"
    with pytest.raises(WorklogError, match="reserved"):
        ledger.edit(capture["id"], project="inbox")
    with pytest.raises(WorklogError, match="Unknown project"):
        ledger.edit(capture["id"], project="typo")
    done = ledger.edit(capture["id"], status="done")
    assert parse_iso(done["closed_at"]) == at("18:00")
    clock[0] = at("19:00")
    assert ledger.edit(capture["id"], status="done")["closed_at"] == done["closed_at"]
    reopened = ledger.edit(capture["id"], status="open", description="")
    assert reopened["closed_at"] is None and reopened["description"] is None


def order(db):
    return [t["title"] for t in sorted(db.tasks(), key=lambda t: t["rank"])]


def test_manual_order_before_after_top(ledger):
    a, b, c = (ledger.add_task(None, name)["id"] for name in "abc")
    assert order(ledger) == ["c", "b", "a"]
    ledger.edit(c, after=a)
    assert order(ledger) == ["b", "a", "c"]
    ledger.edit(c, before=b)
    assert order(ledger) == ["c", "b", "a"]
    ledger.edit(a, top=True)
    assert order(ledger) == ["a", "c", "b"]
    with pytest.raises(WorklogError, match="itself"):
        ledger.edit(a, before=a)
    with pytest.raises(WorklogError, match="only one"):
        ledger.edit(a, before=b, top=True)


def test_order_renumbers_when_gaps_get_too_small(ledger):
    a, b = (ledger.add_task(None, name)["id"] for name in "ab")
    others = [ledger.add_task(None, f"x{i}")["id"] for i in range(3)]
    for _ in range(40):  # halve the same gap until it must be renumbered
        for task in others:
            ledger.edit(task, before=a)
    assert order(ledger) == ["b", "x0", "x1", "x2", "a"]
    ranks = [t["rank"] for t in ledger.tasks()]
    assert len(set(ranks)) == len(ranks)
    assert ledger.edit(b, before=a)["rank"] < ledger.show(a)["rank"]


def test_agent_turn_reopens_done_task_but_heartbeat_and_log_do_not(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    activity(ledger, ts("09:10"))
    task_id = only_task(ledger)["id"]
    ledger.edit(task_id, status="done")
    activity(ledger, ts("09:20"))
    ledger.log(at("10:00"), at("10:30"), task_id=task_id)
    assert ledger.show(task_id)["status"] == "done"
    prompt(ledger, tmp_path / "work", ts("11:00"))
    reopened = ledger.show(task_id)
    assert reopened["status"] == "open" and reopened["closed_at"] is None


def test_assign_moves_inbox_capture_to_the_chat_project(ledger, tmp_path):
    capture = ledger.add_task(None, "Fix the export")
    prompt(ledger, tmp_path / "work" / "repo", ts("09:00"))
    activity(ledger, ts("09:30"))
    info = ledger.assign("claude:s1", task_id=capture["id"])
    assert info["project"] == "example-project"
    task = only_task(ledger)
    assert task["id"] == capture["id"] and task["started"]


def test_assign_keeps_inbox_capture_from_unmapped_directory_or_explicit_project(ledger, tmp_path):
    first = ledger.add_task(None, "First")
    second = ledger.add_task(None, "Second")
    ledger.add_project("example-client")
    prompt(ledger, tmp_path / "elsewhere", ts("09:00"), session="a")
    prompt(ledger, tmp_path / "work", ts("09:00"), session="b")
    assert ledger.assign("claude:a", task_id=first["id"])["project"] == "inbox"
    info = ledger.assign("claude:b", task_id=second["id"], project="example-client")
    assert info["project"] == "example-client"


def test_schema_2_ledger_is_migrated(tmp_path):
    path = tmp_path / "v2.db"
    connection = sqlite3.connect(path)
    schema = """
    CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
    CREATE TABLE counters (day TEXT PRIMARY KEY, value INTEGER NOT NULL);
    CREATE TABLE tasks (
     id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
     title TEXT NOT NULL, ref TEXT, status TEXT NOT NULL,
     auto_title INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE agent_sessions (
     agent TEXT NOT NULL, session_id TEXT NOT NULL, task_id TEXT, cwd TEXT NOT NULL,
     ignored INTEGER NOT NULL DEFAULT 0, context_hash TEXT, created_at TEXT NOT NULL,
     PRIMARY KEY(agent, session_id));
    CREATE TABLE intervals (
     id TEXT PRIMARY KEY, task_id TEXT NOT NULL, started_at TEXT NOT NULL,
     ended_at TEXT NOT NULL, source TEXT NOT NULL, session_id TEXT, note TEXT);
    INSERT INTO projects VALUES (1, 'example-project');
    INSERT INTO tasks VALUES ('ACT-20260901-001', 1, 'Old', NULL, 'done', 0,
     '2026-09-01T09:00:00+00:00');
    INSERT INTO tasks VALUES ('ACT-20260902-001', 1, 'Newer', NULL, 'open', 0,
     '2026-09-02T09:00:00+00:00');
    PRAGMA user_version = 2;
    """
    connection.executescript(schema)
    connection.close()
    db = Ledger(path)
    try:
        assert db.db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert order(db) == ["Newer", "Old"]
        old = db.show("ACT-20260901-001")
        assert old["status"] == "done" and old["closed_at"] is None
        assert old["description"] is None
        db.add_task(None, "Capture")
        assert order(db) == ["Capture", "Newer", "Old"]
    finally:
        db.close()
