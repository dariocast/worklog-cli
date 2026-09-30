import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from worklog_cli.config import default_db, resolve


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("WORKLOG_DB", raising=False)
    monkeypatch.chdir(tmp_path)

    def run(*args, ok=True):
        result = subprocess.run(
            [sys.executable, "-m", "worklog_cli.cli", *args],
            text=True,
            capture_output=True,
            cwd=Path.cwd(),
            env=os.environ.copy(),
        )
        assert result.returncode == (0 if ok else 2), result.stderr
        return result

    return run


def data(result):
    body = json.loads(result.stdout)
    assert body["schema_version"] == 1
    return body["data"]


def test_acceptance(cli, tmp_path):
    cli("project", "add", "example-project")
    started = data(
        cli(
            "start",
            "--project",
            "example-project",
            "--title",
            "Review example configuration",
            "--json",
        )
    )
    task_id = started["id"]
    assert task_id in cli("status").stdout
    assert data(cli("status", "--json"))["active"]
    assert "Stopped" in cli("stop").stdout
    assert task_id in cli("list").stdout
    assert len(data(cli("list", "--json"))) == 1
    cli("start", task_id)
    cli("stop")
    tasks = data(cli("list", "--json"))
    assert len(tasks) == 1 and len(tasks[0]["sessions"]) == 2
    rows = list(csv.DictReader(io.StringIO(cli("export", "--format", "csv").stdout)))
    assert len(rows) == 2 and rows[0]["task_id"] == task_id
    exported = data(cli("export", "--format", "json"))
    assert exported["tasks"] == tasks
    assert exported["projects"][0]["name"] == "example-project"
    assert data(cli("report", "--json"))["duration_seconds"] >= 0
    (tmp_path / ".worklog.toml").write_text(
        'project = "example-project"\ntags = ["demo", "example"]\n'
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    os.chdir(nested)
    detected = data(cli("start", "--title", "Detected", "--json"))
    assert detected["project"] == "example-project"
    assert detected["tags"] == ["demo", "example"]
    cli("stop")


def test_errors_and_global_positions(cli, tmp_path):
    failure = cli("start", "--title", "Missing project", "--json", ok=False)
    assert not failure.stdout
    assert json.loads(failure.stderr)["error"]["code"] == "validation_error"
    assert json.loads(cli("bogus", "--json", ok=False).stderr)["error"]["code"] == "usage_error"
    assert json.loads(cli("--db", "--json", ok=False).stderr)["error"]
    other = str(tmp_path / "other.db")
    cli("--json", "project", "add", "other", "--db", other)
    assert len(data(cli("projects", "--db=" + other, "--json"))) == 1
    assert data(cli("projects", "--json")) == []
    assert cli("--version").stdout.strip() == "WorkLog CLI 0.1.0"
    assert "export" in cli("--help").stdout


def test_config_precedence(cli, tmp_path):
    config = tmp_path / "config" / "worklog"
    config.mkdir(parents=True)
    (config / "config.toml").write_text('project="global"\ntags=["global"]')
    (tmp_path / ".worklog.toml").write_text('project="repo"\ntags=["repo"]')
    assert resolve(None, None).project == "repo"
    assert resolve("explicit", ["explicit"]).tags == ("explicit",)
    assert resolve("explicit", None).project == "explicit"
    assert resolve(None, []).tags == ()
    sub = tmp_path / "child"
    sub.mkdir()
    (sub / ".worklog.toml").write_text('tags=["child"]')
    os.chdir(sub)
    assert resolve(None, None).project == "global"
    assert resolve(None, None).tags == ("child",)


@pytest.mark.parametrize(
    "content",
    ["project = 1", 'tags = "bad"', "tags = [1]", 'project = ""', "unknown = 1", "invalid = ["],
)
def test_bad_config(cli, tmp_path, content):
    (tmp_path / ".worklog.toml").write_text(content)
    result = cli("start", "--title", "test", "--json", ok=False)
    assert json.loads(result.stderr)["error"]


def test_xdg_relative_ignored(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "relative")
    assert default_db() == Path.home() / ".local/share/worklog/worklog.db"


def test_edit_complete_and_export_safety(cli, tmp_path):
    cli("project", "add", "p", "--alias", "alias", "--metadata", '{"team":"a"}')
    task = data(cli("task", "add", "--project", "alias", "--title", "=1+1", "--json"))
    task_id = task["id"]
    cli(
        "edit",
        task_id,
        "--ref",
        "jira",
        "KEY-1",
        "--url",
        "https://example.org",
        "--notes",
        "line1\nline2",
        "--tag",
        "x",
    )
    shown = data(cli("show", task_id, "--json"))
    assert shown["external_references"][0]["system"] == "jira"
    assert shown["notes"] == "line1\nline2"
    assert "KEY-1" in cli("show", task_id).stdout
    cli("resume", task_id)
    cli("complete", task_id)
    assert not data(cli("status", "--json"))["active"]
    cli("resume", task_id, "--json", ok=False)
    cli("edit", task_id, "--status", "todo", "--clear-tags")
    assert data(cli("show", task_id, "--json"))["tags"] == []
    rows = list(csv.DictReader(io.StringIO(cli("export", "--format", "csv").stdout)))
    assert rows[0]["title"] == "'=1+1"
    output = tmp_path / "export.json"
    cli("export", "--format", "json", "--output", str(output))
    before = output.read_bytes()
    cli("export", "--format", "json", "--output", str(output), ok=False)
    assert output.read_bytes() == before
    cli("export", "--format", "csv", "--json", ok=False)
    cli("today", "--timezone", "Europe/Paris", "--json")
    cli("today", "--timezone", "Invalid", "--json", ok=False)


def test_missing_and_empty_inputs(cli):
    cli("project", "add", "p")
    cli("start", "--project", "p", "--title", "   ", ok=False)
    cli("start", "--project", "p", ok=False)
    cli("project", "add", "p", ok=False)
    cli("project", "add", "q", "--metadata", "[]", ok=False)
    cli("show", "missing", ok=False)
    cli("edit", "missing", "--url", "https://example.org", ok=False)
    task = data(cli("task", "add", "--project", "p", "--title=--json", "--json"))
    assert task["title"] == "--json"
    cli("start", task["id"], "--title", "ignored", ok=False)
    assert data(cli("status", "--json"))["active"] is False


@pytest.mark.parametrize("metadata", ['{"x": NaN}', '{"x": Infinity}', '{"x": 1e999}'])
def test_metadata_is_strict_json(cli, metadata):
    cli("project", "add", "p", "--metadata", metadata, "--json", ok=False)
    assert data(cli("projects", "--json")) == []


def test_invalid_timezone_path(cli):
    result = cli("today", "--timezone", "/etc/passwd", "--json", ok=False)
    assert json.loads(result.stderr)["error"]["code"] == "validation_error"
