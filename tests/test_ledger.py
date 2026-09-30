import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from worklog_cli.errors import WorklogError
from worklog_cli.ledger import Ledger
from worklog_cli.reporting import report


@pytest.fixture
def ledger(tmp_path):
    clock = [datetime(2026, 9, 30, 10, tzinfo=UTC)]
    store = Ledger(tmp_path / "ledger.db", lambda: clock[0])
    store.add_project("example-project", ["example"], {"team": "demo"})
    yield store, clock
    store.close()


def test_two_sessions_one_task(ledger):
    db, clock = ledger
    task = db.start(None, "example", "Review configuration", ["debug", "debug"])
    assert task["id"] == "ACT-20260930-001"
    clock[0] += timedelta(minutes=35)
    assert db.stop()["session"]["duration_seconds"] == 2100
    clock[0] += timedelta(hours=3)
    db.start(task["id"])
    clock[0] += timedelta(minutes=42)
    result = db.stop()
    assert result["task"]["duration_seconds"] == 4620
    assert len(db.tasks()) == 1
    assert len(result["task"]["sessions"]) == 2
    assert result["task"]["tags"] == ["debug"]
    assert db.status() == {"active": False, "task": None}
    assert db.stop()["stopped"] is False


def test_conflict_does_not_create_task_or_consume_id(ledger):
    db, _ = ledger
    first = db.start(None, "example", "First")
    with pytest.raises(WorklogError, match="already active"):
        db.start(None, "example", "Second")
    assert len(db.tasks()) == 1
    db.stop()
    second = db.start(None, "example", "Second")
    assert first["id"].endswith("001") and second["id"].endswith("002")


def test_switch_rolls_back_and_completion_reopen(ledger):
    db, clock = ledger
    task = db.start(None, "example", "First")
    clock[0] += timedelta(minutes=5)
    with pytest.raises(WorklogError, match="Unknown task"):
        db.start("missing", switch=True)
    assert db.status()["task"]["id"] == task["id"]
    second = db.add_task("example", "Second", [])
    db.start(second["id"], switch=True)
    assert db.show(task["id"])["duration_seconds"] == 300
    done = db.edit(second["id"], status="done")
    assert done["completed_at"] and not db.status()["active"]
    with pytest.raises(WorklogError, match="complete"):
        db.start(second["id"])
    db.edit(second["id"], status="todo")
    db.start(second["id"])
    with pytest.raises(WorklogError, match="Stop"):
        db.edit(second["id"], status="todo")


def test_alias_collision_and_references(ledger):
    db, _ = ledger
    with pytest.raises(WorklogError, match="already exists"):
        db.add_project("example", [], {})
    assert len(db.projects()) == 1
    task = db.add_task("example", "Title", ["a"])
    db.edit(task["id"], notes="Notes", tags=["b"], reference=("jira", "KEY-1", None))
    result = db.edit(task["id"], reference=("jira", "KEY-1", "https://example.org"))
    assert result["tags"] == ["b"]
    assert result["notes"] == "Notes"
    assert len(result["external_references"]) == 1
    assert result["external_references"][0]["url"] == "https://example.org"
    assert db.tasks(tag="a") == []
    assert len(db.tasks(project="example", status="todo", tag="b")) == 1


def test_clock_backward_rollback(ledger):
    db, clock = ledger
    db.start(None, "example", "Title")
    clock[0] -= timedelta(seconds=1)
    with pytest.raises(WorklogError, match="Clock"):
        db.stop()
    assert db.status()["active"]
    clock[0] += timedelta(minutes=2)
    db.stop()
    clock[0] -= timedelta(seconds=1)
    with pytest.raises(WorklogError, match="Clock"):
        db.start(None, "example", "Other")
    assert len(db.tasks()) == 1


def test_sql_constraints_and_private_file(ledger):
    db, _ = ledger
    task = db.start(None, "example", "Title")
    with pytest.raises(sqlite3.IntegrityError):
        db.db.execute(
            "INSERT INTO sessions(task_id,started_at) VALUES (?,?)", (task["id"], db.now())
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.db.execute("INSERT INTO task_tags VALUES ('missing','missing')")
    with pytest.raises(sqlite3.IntegrityError):
        db.db.execute("UPDATE tasks SET status='done'")
    path = Path(db.db.execute("PRAGMA database_list").fetchone()[2])
    assert path.stat().st_mode & 0o777 == 0o600


def test_newer_schema_rejected(tmp_path):
    path = tmp_path / "future.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version=99")
    db.close()
    with pytest.raises(WorklogError, match="newer"):
        Ledger(path)
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 99
    db.close()


def test_concurrent_start(tmp_path):
    path = tmp_path / "race.db"
    db = Ledger(path)
    db.add_project("p", [], {})
    db.close()

    def run(index):
        store = Ledger(path)
        try:
            store.start(None, "p", f"Task {index}")
            return True
        except WorklogError:
            return False
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(run, range(4)))
    assert outcomes.count(True) == 1
    db = Ledger(path)
    assert len(db.tasks()) == 1
    db.close()


def test_concurrent_initialization_and_task_ids(tmp_path):
    path = tmp_path / "fresh.db"

    def initialize(_):
        store = Ledger(path)
        store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(initialize, range(4)))
    db = Ledger(path)
    db.add_project("p", [], {})
    db.close()

    def create(_):
        store = Ledger(path)
        try:
            return store.add_task("p", "Title", [])["id"]
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(12)))
    assert len(set(ids)) == 12


def test_report_clips_midnight_and_running(ledger):
    db, clock = ledger
    clock[0] = datetime(2026, 9, 29, 23, 30, tzinfo=UTC)
    db.start(None, "example", "Night")
    clock[0] += timedelta(hours=2)
    result = report(db.tasks(), "2026-09-30", "2026-09-30", "UTC", clock[0])
    assert result["duration_seconds"] == 5400
    assert result["tasks"][0]["session_count"] == 1
    assert report(db.tasks(), "2026-10-01", None, "UTC", clock[0])["tasks"] == []


@pytest.mark.parametrize("day,hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_report_dst(day, hours):
    task = {
        "id": "1",
        "project": "p",
        "title": "DST",
        "sessions": [
            {
                "started_at": f"{day}T00:00:00+01:00" if hours == 23 else f"{day}T00:00:00+02:00",
                "ended_at": None,
            }
        ],
    }
    now = datetime.fromisoformat(day).replace(tzinfo=UTC) + timedelta(days=2)
    result = report([task], day, day, "Europe/Paris", now)
    assert result["duration_seconds"] == hours * 3600


@pytest.mark.parametrize(
    "start,end,zone",
    [("bad", None, "UTC"), ("2026-10-01", "2026-09-30", "UTC"), (None, None, "Bad/Zone")],
)
def test_invalid_reports(start, end, zone):
    with pytest.raises(WorklogError):
        report([], start, end, zone, datetime.now(UTC))


def test_complete_retry_preserves_timestamp(ledger):
    db, clock = ledger
    task = db.add_task("example", "Finish", [])
    original = db.edit(task["id"], status="done")["completed_at"]
    clock[0] += timedelta(hours=1)
    assert db.edit(task["id"], status="done")["completed_at"] == original
    db.edit(task["id"], status="todo")
    assert db.edit(task["id"], status="done")["completed_at"] != original
