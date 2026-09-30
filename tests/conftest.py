from datetime import datetime

import pytest

from worklog_cli.config import Config
from worklog_cli.ledger import Ledger


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the real ledger, config, state or agent settings."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.delenv("WORKLOG_DB", raising=False)
    return tmp_path


def at(text: str) -> datetime:
    return datetime.fromisoformat(f"2026-09-30T{text}:00+00:00")


@pytest.fixture
def clock():
    return [at("18:00")]


@pytest.fixture
def ledger(tmp_path, clock):
    config = Config(
        paths={str(tmp_path / "work"): "example-project", str(tmp_path / "own"): "ignore"}
    )
    db = Ledger(tmp_path / "ledger.db", config, clock=lambda: clock[0])
    yield db
    db.close()
