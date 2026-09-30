"""Append-only spool for hook events that are not (yet) in the ledger.

Heartbeats are always spooled to keep hooks fast; other events are spooled only
when writing to SQLite fails. Any command that opens the ledger drains it.
"""

import contextlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from worklog_cli.config import state_dir
from worklog_cli.timeutil import iso, utcnow


def spool_file() -> Path:
    return state_dir() / "spool.jsonl"


def error_log() -> Path:
    return state_dir() / "errors.log"


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def append(event: dict[str, Any]) -> None:
    _append(spool_file(), json.dumps(event, separators=(",", ":")))


def log_error(message: str) -> None:
    with contextlib.suppress(OSError):
        _append(error_log(), f"{iso(utcnow())} {' '.join(message.split())}")


def pending() -> int:
    count = 0
    for path in spool_file().parent.glob("spool*.jsonl"):
        try:
            count += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)
        except OSError:
            continue
    return count


def errors() -> list[str]:
    try:
        return error_log().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def drain(apply: Callable[[dict[str, Any]], None]) -> None:
    """Apply spooled events oldest first; on failure keep the rest for later."""
    source = spool_file()
    claimed = source.with_name(f"spool.{os.getpid()}.{time.time_ns()}.processing.jsonl")
    with contextlib.suppress(FileNotFoundError):
        os.replace(source, claimed)
    candidates = [
        path
        for path in source.parent.glob("spool.*.processing.jsonl")
        if path == claimed or not _owner_alive(path)
    ]
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime_ns):
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
        for index, line in enumerate(lines):
            try:
                event = json.loads(line)
            except ValueError:
                log_error(f"Dropped malformed spool line: {line[:200]}")
                continue
            try:
                apply(event)
            except Exception as exc:
                log_error(f"Spool replay deferred: {exc}")
                stamp = path.stat().st_mtime_ns
                path.write_text("\n".join(lines[index:]) + "\n", encoding="utf-8")
                os.utime(path, ns=(stamp, stamp))
                return
        path.unlink(missing_ok=True)


def _owner_alive(path: Path) -> bool:
    """True while another live process is replaying this file."""
    try:
        pid = int(path.name.split(".")[1])
        if pid == os.getpid():
            return False
        os.kill(pid, 0)
    except (ValueError, IndexError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True
