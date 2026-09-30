"""Entry point for agent hooks. Never fails, never blocks, never reads prompts.

``prompt`` (UserPromptSubmit) starts a turn and may print context for the chat.
``activity`` (PostToolUse, Stop) only appends a heartbeat to the spool.
"""

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from worklog_cli import config, spool
from worklog_cli.ledger import AGENTS, Ledger
from worklog_cli.timeutil import iso, utcnow


def event_from(kind: str, agent: str, stream: TextIO) -> dict[str, Any]:
    try:
        payload = json.loads(stream.read() or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    # Only identifiers and the directory are kept; prompt text is never read.
    return {
        "event": kind,
        "agent": agent,
        "session_id": str(payload.get("session_id") or ""),
        "cwd": str(payload.get("cwd") or ""),
        "ts": iso(utcnow()),
    }


def run(kind: str, agent: str, db: Path, stdin: TextIO, stdout: TextIO) -> int:
    event: dict[str, Any] | None = None
    try:
        if kind not in ("prompt", "activity") or agent not in AGENTS:
            raise ValueError(f"unsupported hook {kind} --agent {agent}")
        event = event_from(kind, agent, stdin)
        if not event["session_id"]:
            raise ValueError("hook payload without session_id")
        if kind == "activity":
            spool.append(event)
            return 0
        ledger = Ledger(db, config.load())
        try:
            ledger.drain()
            text = ledger.apply_event(event)
        finally:
            ledger.close()
        if text:
            stdout.write(text + "\n")
    except Exception as exc:
        spool.log_error(f"hook {kind} {agent}: {exc}")
        if event is not None and event["session_id"]:
            try:
                spool.append(event)
            except OSError as spool_exc:
                spool.log_error(f"spool append failed: {spool_exc}")
    return 0


def main(kind: str, agent: str, db: Path) -> int:
    return run(kind, agent, db, sys.stdin, sys.stdout)
