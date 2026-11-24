"""The agentic record (0.5): chats of a task, chat titles, forks, review, links, stalls."""

import io
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import at

from worklog_cli import hooks
from worklog_cli.board import board_data, dispatch, status_data
from worklog_cli.config import Config
from worklog_cli.errors import WorklogError
from worklog_cli.ledger import SCHEMA_VERSION, Ledger
from worklog_cli.timeutil import parse_iso


def prompt(db, cwd, when, session="s1", agent="claude", **extra):
    event = {"event": "prompt", "agent": agent, "session_id": session, "cwd": str(cwd), "ts": when}
    return db.apply_event({**event, **extra})


def activity(db, when, session="s1", agent="claude"):
    db.apply_event({"event": "activity", "agent": agent, "session_id": session, "ts": when})


def ts(text):
    return at(text).isoformat()


def only_task(db):
    tasks = db.tasks()
    assert len(tasks) == 1
    return tasks[0]


# Hook payloads


def test_hook_keeps_title_and_parent_but_never_prompt_text():
    claude = {
        "session_id": "s1",
        "cwd": "/tmp/x",
        "session_title": "Fictional refactor",
        "prompt": "secret prompt text",
    }
    event = hooks.event_from("prompt", "claude", io.StringIO(json.dumps(claude)))
    assert event["title"] == "Fictional refactor"
    assert "secret" not in json.dumps(event)

    agy = {
        "conversationId": "c2",
        "cwd": "/tmp/x",
        "parentConversationId": "c1",
        "lastUserInput": "secret prompt text",
    }
    event = hooks.event_from("prompt", "agy", io.StringIO(json.dumps(agy)))
    assert event["session_id"] == "c2" and event["parent"] == "c1"
    assert "secret" not in json.dumps(event)


def test_hook_without_title_or_parent_has_none():
    payload = {"session_id": "s1", "cwd": "/tmp/x", "session_title": "  "}
    event = hooks.event_from("prompt", "codex", io.StringIO(json.dumps(payload)))
    assert event["title"] is None and event["parent"] is None


# Chats of a task


def test_chats_of_a_task_say_where_when_and_how_to_resume(ledger, tmp_path, clock):
    work = tmp_path / "work" / "repo"
    prompt(ledger, work, ts("09:00"), title="Fictional refactor")
    activity(ledger, ts("09:20"))
    prompt(ledger, work, ts("09:30"), session="s2", agent="codex")
    task = ledger.session_info("claude", "s1")
    task["id"] = task["task_id"]
    ledger.assign("codex:s2", task_id=task["id"])
    activity(ledger, ts("09:40"), session="s2", agent="codex")

    clock[0] = at("09:50")
    chats = ledger.show(task["id"])["chats"]
    assert [c["session"] for c in chats] == ["claude:s1", "codex:s2"]
    claude, codex = chats
    assert claude["title"] == "Fictional refactor" and codex["title"] is None
    assert claude["cwd"] == str(work)
    assert parse_iso(claude["first_activity"]) == at("09:00")
    assert parse_iso(claude["last_activity"]) == at("09:20")
    assert claude["duration_seconds"] == 20 * 60
    assert claude["task_id"] == task["id"]
    assert not claude["active"]  # last turn ended 30 minutes ago, idle threshold is 15
    assert codex["active"]
    assert claude["resume"] == f"cd '{work}' && claude --resume s1"
    assert codex["resume"] == f"cd '{work}' && codex resume s2"


def test_title_follows_renames_and_survives_prompts_without_one(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"), title="First name")
    prompt(ledger, tmp_path / "work", ts("09:10"), title="Second name")
    prompt(ledger, tmp_path / "work", ts("09:20"))
    assert ledger.show(only_task(ledger)["id"])["chats"][0]["title"] == "Second name"


def test_chat_with_time_moved_elsewhere_stays_listed(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    activity(ledger, ts("09:10"))
    first = ledger.edit(only_task(ledger)["id"], title="Named, so it is kept")["id"]
    other = ledger.add_task("example-project", "Another task")["id"]
    prompt(ledger, tmp_path / "work", ts("10:00"))
    ledger.assign("claude:s1", task_id=other)  # moves all of the chat's turns
    interval = ledger.show(other)["intervals"][0]["id"]
    ledger.move(interval, first)
    chats = ledger.show(first)["chats"]
    assert [(c["session"], c["task_id"]) for c in chats] == [("claude:s1", other)]


def test_agy_resume_and_short_id_without_title(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"), session="conv-1", agent="agy")
    chat = ledger.show(only_task(ledger)["id"])["chats"][0]
    assert chat["resume"].endswith("agy --conversation conv-1")
    assert chat["title"] is None


def test_codex_titles_come_from_its_session_index(ledger, tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    lines = [
        {"id": "s2", "thread_name": "Old name", "updated_at": "2026-09-30T08:00:00Z"},
        "not json",
        {"id": "s9", "thread_name": "Unrelated", "updated_at": "2026-09-30T08:00:00Z"},
        {"id": "s2", "thread_name": "New name", "updated_at": "2026-09-30T09:00:00Z"},
    ]
    (codex_home / "session_index.jsonl").write_text(
        "\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n"
    )
    prompt(ledger, tmp_path / "work", ts("09:00"), session="s2", agent="codex")
    assert ledger.show(only_task(ledger)["id"])["chats"][0]["title"] == "New name"


def test_missing_or_broken_codex_index_falls_back_silently(ledger, tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    prompt(ledger, tmp_path / "work", ts("09:00"), session="s2", agent="codex")
    task_id = only_task(ledger)["id"]
    assert ledger.show(task_id)["chats"][0]["title"] is None
    (codex_home / "session_index.jsonl").write_bytes(b"\xff\xfe broken")
    assert ledger.show(task_id)["chats"][0]["title"] is None


def test_board_cards_carry_their_chats(ledger, tmp_path, clock):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    activity(ledger, ts("09:05"))
    clock[0] = at("09:06")
    task = board_data(ledger)["tasks"][0]
    assert [c["session"] for c in task["chats"]] == ["claude:s1"]
    assert task["chats"][0]["active"]


# Forks


def test_fork_with_known_parent_joins_the_parents_task(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"), session="c1", agent="agy")
    parent_task = only_task(ledger)["id"]
    prompt(ledger, tmp_path / "work", ts("09:30"), session="c2", agent="agy", parent="c1")
    assert only_task(ledger)["id"] == parent_task
    assert ledger.session_info("agy", "c2")["task_id"] == parent_task


def test_fork_with_unknown_or_ignored_parent_gets_its_own_task(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"), session="c2", agent="agy", parent="nobody")
    assert len(ledger.tasks()) == 1
    ledger.ignore("agy:c3")
    prompt(ledger, tmp_path / "work", ts("09:10"), session="c4", agent="agy", parent="c3")
    assert len(ledger.tasks()) == 2


def test_note_tells_forks_to_rejoin_the_earlier_task(ledger, tmp_path):
    note = prompt(ledger, tmp_path / "work", ts("09:00"))
    assert "earlier WorkLog note" in note and "assign --session claude:s1 --task ID" in note


# Review


def test_review_is_a_state_between_progress_and_waiting(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    task_id = only_task(ledger)["id"]
    task = ledger.edit(task_id, review=True)
    assert task["review"] and not task["waiting"]
    assert board_data(ledger)["tasks"][0]["column"] == "review"
    status = status_data(ledger)
    assert status["counts"]["review"] == 1 and status["columns"]["review"][0]["id"] == task_id

    task = ledger.edit(task_id, waiting=True)
    assert task["waiting"] and not task["review"]
    task = ledger.edit(task_id, review=True)
    assert task["review"] and not task["waiting"]
    assert not ledger.edit(task_id, review=False)["review"]


def test_review_rules_follow_waiting(ledger, tmp_path):
    ledger.add_project("example-project")
    capture = ledger.add_task(None, "Fictional capture")["id"]
    with pytest.raises(WorklogError, match="project"):
        ledger.edit(capture, review=True)
    done = ledger.add_task("example-project", "Closed")["id"]
    ledger.edit(done, status="done")
    with pytest.raises(WorklogError, match="Reopen"):
        ledger.edit(done, review=True)
    assert ledger.edit(done, status="open", review=True)["review"]
    assert not ledger.edit(done, status="done")["review"]


def test_new_turn_clears_review_but_heartbeats_and_logs_do_not(ledger, tmp_path, clock):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    task_id = only_task(ledger)["id"]
    ledger.assign("claude:s1", review=True)
    activity(ledger, ts("09:10"))
    ledger.log(at("10:00"), at("10:30"), task_id=task_id)
    assert ledger.show(task_id)["review"]
    prompt(ledger, tmp_path / "work", ts("11:00"))
    assert not ledger.show(task_id)["review"]


# Links


def test_links_keep_urls_and_normalize_paths(ledger, tmp_path):
    ledger.add_project("example-project")
    task_id = ledger.add_task("example-project", "Spec work")["id"]
    ledger.link(task_id, "https://tracker.example.com/DEMO-1")
    ledger.link(task_id, "~/specs/001-demo/spec.md")
    task = ledger.link(task_id, "https://tracker.example.com/DEMO-1")  # already there
    home = Path.home()
    assert task["links"] == [
        "https://tracker.example.com/DEMO-1",
        str(home / "specs/001-demo/spec.md"),
    ]
    task = ledger.link(task_id, "~/specs/001-demo/spec.md", remove=True)
    assert task["links"] == ["https://tracker.example.com/DEMO-1"]
    assert ledger.tasks()[0]["links"] == task["links"]
    with pytest.raises(WorklogError, match="not linked"):
        ledger.link(task_id, "~/nothing.md", remove=True)
    with pytest.raises(WorklogError, match="empty"):
        ledger.link(task_id, "  ")


def test_links_appear_in_the_brief_and_go_with_the_task(ledger):
    ledger.add_project("example-project")
    task_id = ledger.add_task("example-project", "Spec work")["id"]
    ledger.link(task_id, "https://tracker.example.com/DEMO-1")
    assert "https://tracker.example.com/DEMO-1" in ledger.brief(task_id)["text"]
    ledger.remove_task(task_id)
    assert ledger.db.execute("SELECT count(*) FROM links").fetchone()[0] == 0


# Stalled tasks


def test_stalled_is_derived_from_the_last_recorded_time(ledger, tmp_path, clock):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    activity(ledger, ts("09:30"))
    task_id = only_task(ledger)["id"]
    clock[0] = at("09:30") + timedelta(days=6)
    task = only_task(ledger)
    assert parse_iso(task["last_activity"]) == at("09:30") and not task["stalled"]
    clock[0] = at("09:30") + timedelta(days=8)
    assert only_task(ledger)["stalled"]
    ledger.edit(task_id, review=True)
    assert only_task(ledger)["stalled"]
    ledger.edit(task_id, waiting=True)
    assert not only_task(ledger)["stalled"]
    assert ledger.add_task("example-project", "Not started")["stalled"] is False


def test_stall_threshold_comes_from_config(tmp_path, clock):
    config = Config(paths={str(tmp_path / "work"): "example-project"}, stall_after="2d")
    db = Ledger(tmp_path / "other.db", config, clock=lambda: clock[0])
    try:
        prompt(db, tmp_path / "work", ts("09:00"))
        activity(db, ts("09:30"))
        clock[0] = at("09:30") + timedelta(days=3)
        assert only_task(db)["stalled"]
    finally:
        db.close()


def test_stall_threshold_accepts_days_or_durations():
    assert Config(stall_after="7d").stall_seconds == 7 * 86400
    assert Config(stall_after="36h").stall_seconds == 36 * 3600
    with pytest.raises(WorklogError):
        Config(stall_after="soon").stall_seconds  # noqa: B018


# Board API


def test_board_api_sets_review_and_links(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    task_id = only_task(ledger)["id"]
    task = dispatch(ledger, "PATCH", f"/api/tasks/{task_id}", {}, {"review": True})
    assert task["review"]
    with pytest.raises(WorklogError, match="boolean"):
        dispatch(ledger, "PATCH", f"/api/tasks/{task_id}", {}, {"review": "yes"})
    path = f"/api/tasks/{task_id}/links"
    task = dispatch(ledger, "POST", path, {}, {"target": "https://example.com/x"})
    assert task["links"] == ["https://example.com/x"]
    task = dispatch(ledger, "POST", path, {}, {"target": "https://example.com/x", "remove": True})
    assert task["links"] == []


# Migration


SCHEMA_5 = """
CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE counters (day TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
 title TEXT NOT NULL, ref TEXT, status TEXT NOT NULL CHECK(status IN ('open','done')),
 auto_title INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 description TEXT, rank REAL NOT NULL DEFAULT 0, closed_at TEXT, next_step TEXT,
 waiting INTEGER NOT NULL DEFAULT 0, dropped INTEGER NOT NULL DEFAULT 0, original TEXT
);
CREATE TABLE agent_sessions (
 agent TEXT NOT NULL, session_id TEXT NOT NULL, task_id TEXT REFERENCES tasks(id),
 cwd TEXT NOT NULL, ignored INTEGER NOT NULL DEFAULT 0, context_hash TEXT,
 created_at TEXT NOT NULL, PRIMARY KEY(agent, session_id)
);
CREATE TABLE intervals (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('claude','codex','agy','manual')),
 session_id TEXT, note TEXT
);
INSERT INTO projects(id, name) VALUES (1, 'example-project');
INSERT INTO tasks(id, project_id, title, status, created_at)
 VALUES ('ACT-20260901-001', 1, 'Old', 'open', '2026-09-01T09:00:00+00:00');
INSERT INTO agent_sessions(agent, session_id, task_id, cwd, created_at)
 VALUES ('claude', 's1', 'ACT-20260901-001', '/tmp/x', '2026-09-01T09:00:00+00:00');
PRAGMA user_version = 5;
"""


def test_schema_5_ledger_is_migrated(tmp_path):
    path = tmp_path / "v5.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA_5)
    connection.close()
    db = Ledger(path)
    try:
        assert db.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 6
        task = db.show("ACT-20260901-001")
        assert task["review"] is False and task["links"] == []
        assert task["chats"][0]["session"] == "claude:s1" and task["chats"][0]["title"] is None
    finally:
        db.close()


# CLI


def test_cli_links_review_and_chats(isolated):
    from test_cli import data, hook, run

    repo = isolated / "work" / "example-repo"
    repo.mkdir(parents=True)
    run("map", str(isolated / "work"), "example-project")
    payload = {"session_id": "abc", "cwd": str(repo), "session_title": "Fictional spec chat"}
    hook("prompt", "abc", repo, payload=json.dumps(payload))
    hook("activity", "abc", repo)
    task_id = data(run("list", "--json"))[0]["id"]

    linked = data(run("link", task_id, "https://tracker.example.com/DEMO-1", "--json"))
    assert linked["links"] == ["https://tracker.example.com/DEMO-1"]
    assert (
        data(run("link", task_id, "--rm", "https://tracker.example.com/DEMO-1", "--json"))["links"]
        == []
    )
    run("link", task_id, "specs/001-demo", cwd=repo)
    assert data(run("show", task_id, "--json"))["links"] == [str(repo / "specs/001-demo")]

    assigned = data(run("assign", "--session", "claude:abc", "--review", "--json"))
    assert assigned["task_id"] == task_id
    assert data(run("show", task_id, "--json"))["review"]
    status = run("status").stdout
    assert "Review 1" in status
    assert not data(run("edit", task_id, "--no-review", "--json"))["review"]

    shown = run("show", task_id).stdout
    assert "Fictional spec chat" in shown and "claude --resume abc" in shown
    assert str(repo / "specs/001-demo") in shown
    chats = data(run("show", task_id, "--json"))["chats"]
    assert chats[0]["title"] == "Fictional spec chat"


def test_provisional_task_with_links_is_kept_when_its_chat_moves(ledger, tmp_path):
    prompt(ledger, tmp_path / "work", ts("09:00"))
    provisional = only_task(ledger)["id"]
    ledger.link(provisional, "https://example.com/spec")
    other = ledger.add_task("example-project", "Planned")["id"]
    ledger.assign("claude:s1", task_id=other)
    assert ledger.show(provisional)["links"] == ["https://example.com/spec"]
