import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run(*args, stdin="", ok=True, cwd=None):
    result = subprocess.run(
        [sys.executable, "-m", "worklog_cli.cli", *args],
        input=stdin,
        text=True,
        capture_output=True,
        cwd=cwd,
        env=os.environ.copy(),
    )
    assert result.returncode == (0 if ok else 2), result.stderr
    return result


def data(result):
    body = json.loads(result.stdout)
    assert body["schema_version"] == 1
    return body["data"]


def hook(kind, session, cwd, agent="claude", payload=None):
    body = (
        payload
        if payload is not None
        else json.dumps({"session_id": session, "cwd": str(cwd), "prompt": "secret prompt text"})
    )
    result = run("hook", kind, "--agent", agent, stdin=body)
    assert result.stderr == ""
    return result.stdout


def test_agent_flow_end_to_end(isolated):
    repo = isolated / "work" / "example-repo"
    repo.mkdir(parents=True)
    mapped = data(run("map", str(isolated / "work"), "example-project", "--json"))
    assert mapped["moved_sessions"] == 0
    first = hook("prompt", "abc", repo)
    assert "[WorkLog]" in first and "claude:abc" in first and "example-project" in first
    assert hook("activity", "abc", repo) == ""
    assert hook("prompt", "abc", repo) == ""  # context is injected once
    hook("activity", "abc", repo)
    info = data(
        run(
            "assign", "--session", "claude:abc", "--title", "Fix login", "--ref", "DEMO-1", "--json"
        )
    )
    assert info["title"] == "Fix login"
    tasks = data(run("list", "--open", "--json"))
    assert [t["ref"] for t in tasks] == ["DEMO-1"]
    ledger_bytes = (isolated / "data" / "worklog" / "ledger.db").read_bytes()
    assert b"secret prompt text" not in ledger_bytes
    assert list((isolated / "state" / "worklog").glob("spool*")) == []  # drained by commands
    entries = data(run("report", "--today", "--json"))["entries"]
    assert [(e["key"], e["title"]) for e in entries] == [("DEMO-1", "Fix login")]


def test_hooks_never_fail_or_print_on_errors(isolated):
    assert hook("prompt", "x", isolated, payload="not json") == ""
    assert hook("prompt", "x", isolated, agent="unknown") == ""
    assert run("hook").returncode == 0
    errors = (isolated / "state" / "worklog" / "errors.log").read_text()
    assert "without session_id" in errors and "unsupported hook" in errors
    doctor = data(run("doctor", "--json"))
    assert not doctor["ok"]
    assert any(c["name"] == "hook errors" and not c["ok"] for c in doctor["checks"])


def test_unmapped_chat_lands_in_inbox(isolated):
    hook("prompt", "s", isolated / "somewhere")
    inbox = data(run("inbox", "--json"))
    assert [i["session"] for i in inbox] == ["claude:s"]
    run("map", str(isolated / "somewhere"), "ignore")
    assert data(run("inbox", "--json")) == []
    config = (isolated / "config" / "worklog" / "config.toml").read_text()
    assert '"ignore"' in config


def test_manual_logging_and_report_formats(isolated):
    run("project", "add", "example-project")
    logged = data(
        run(
            "log",
            "1h30",
            "--day",
            "yesterday",
            "--project",
            "example-project",
            "--title",
            "Workshop",
            "--note",
            "=cmd()",
            "--json",
        )
    )
    assert logged["duration_seconds"] == 5400
    task_id = logged["task_id"]
    run("log", "10:00-10:20", "--day", "yesterday", "--task", task_id)
    text = run("report", "--yesterday").stdout
    assert "example-project" in text and "1h50" in text and "Workshop" in text
    rounded = data(run("report", "--yesterday", "--round", "1h", "--json"))
    assert rounded["entries"][0]["duration_seconds"] == 7200
    rows = list(csv.DictReader(io.StringIO(run("report", "--yesterday", "--format", "csv").stdout)))
    assert rows[0]["minutes"] == "110" and rows[0]["notes"] == "'=cmd()"
    listed = run("intervals", "--yesterday").stdout.splitlines()
    assert len(listed) == 2
    removed = run("rm", listed[0].split()[0]).stdout
    assert "worklog log" in removed and "--task " + task_id in removed
    assert len(run("intervals", "--yesterday").stdout.splitlines()) == 1


def test_structured_errors(isolated):
    result = run("log", "90", "--task", "ACT-1", "--json", ok=False)
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "validation_error"
    result = run("report", "--today", "--from", "2026-01-01", "--json", ok=False)
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"
    assert run("--version").stdout.strip().startswith("WorkLog CLI 0.2")


@pytest.fixture
def agent_homes(isolated):
    home = Path(os.environ["HOME"])
    claude, codex = home / ".claude", home / ".codex"
    claude.mkdir()
    codex.mkdir()
    other = {"type": "command", "command": "/usr/local/bin/other-tool"}
    (claude / "settings.json").write_text(
        json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [other]}]}}, indent=2) + "\n"
    )
    return claude / "settings.json", codex / "hooks.json", other


def test_setup_is_reviewable_idempotent_and_reversible(agent_homes):
    claude, codex, other = agent_homes
    before = claude.read_text()
    preview = data(run("setup", "--dry-run", "--json"))
    assert {c["agent"] for c in preview["changes"]} == {"claude", "codex"}
    assert claude.read_text() == before
    assert run("setup", "--json", ok=False).stderr  # refuses without --yes
    applied = data(run("setup", "--yes", "--json"))
    assert len(applied["applied"]) == 2
    settings = json.loads(claude.read_text())
    assert settings["model"] == "x"
    assert other in [h for g in settings["hooks"]["Stop"] for h in g["hooks"]]
    commands = [h["command"] for g in settings["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert len(commands) == 1 and commands[0].endswith(" hook prompt --agent claude")
    assert json.loads(codex.read_text())["hooks"]["PostToolUse"]
    assert list(claude.parent.glob("settings.json.worklog-backup-*"))
    again = data(run("setup", "--yes", "--json"))
    assert again["applied"] == []
    doctor = {c["name"]: c for c in data(run("doctor", "--json"))["checks"]}
    assert doctor["claude hooks"]["ok"] and doctor["codex hooks"]["ok"]
    data(run("setup", "--uninstall", "--yes", "--json"))
    assert json.loads(claude.read_text()) == json.loads(before)
    assert codex.read_text() == ""


def test_setup_refuses_invalid_settings(agent_homes):
    claude, _, _ = agent_homes
    claude.write_text("{broken")
    result = run("setup", "--yes", "--json", ok=False)
    assert json.loads(result.stderr)["error"]["code"] == "config_error"
    assert claude.read_text() == "{broken"
