"""Noninteractive command line interface with a versioned JSON contract."""

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from worklog_cli import __version__
from worklog_cli.config import default_db, resolve
from worklog_cli.errors import WorklogError
from worklog_cli.exporting import csv_text, json_text
from worklog_cli.ledger import Ledger
from worklog_cli.reporting import report

STATUSES = ("todo", "in_progress", "done")


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise WorklogError(message, "usage_error")


def parser() -> Parser:
    root = Parser(prog="worklog", description="WorkLog CLI — local task and session ledger")
    root.add_argument("--version", action="version", version=f"WorkLog CLI {__version__}")
    root.add_argument("--json", action="store_true", help="Versioned JSON output (any position)")
    root.add_argument("--db", help="Database path (any position); overrides WORKLOG_DB")
    commands = root.add_subparsers(dest="command", required=True)
    project = commands.add_parser("project", help="Manage projects")
    add = project.add_subparsers(dest="action", required=True).add_parser("add")
    add.add_argument("name")
    add.add_argument("--alias", action="append", default=[])
    add.add_argument("--metadata", default="{}", help="JSON object")
    commands.add_parser("projects", help="List registered projects and aliases")
    task_add = commands.add_parser("task", help="Create a task without starting a session")
    task_add = task_add.add_subparsers(dest="action", required=True).add_parser("add")
    for command in (task_add, commands.add_parser("start", help="Create or resume a task")):
        if command is not task_add:
            command.add_argument("task_id", nargs="?")
        command.add_argument("--project")
        command.add_argument("--title", required=command is task_add)
        command.add_argument("--description", default=None)
        command.add_argument("--tag", action="append", default=None)
    for name in ("resume", "switch", "show", "complete"):
        commands.add_parser(name).add_argument("task_id")
    for name in ("stop", "status"):
        commands.add_parser(name)
    edit = commands.add_parser("edit", help="Edit task fields; tags/notes replace existing values")
    edit.add_argument("task_id")
    for name in ("title", "description", "notes"):
        edit.add_argument(f"--{name}")
    edit.add_argument("--status", choices=STATUSES)
    tags = edit.add_mutually_exclusive_group()
    tags.add_argument("--tag", action="append")
    tags.add_argument("--clear-tags", action="store_true")
    edit.add_argument("--ref", nargs=2, metavar=("SYSTEM", "REFERENCE"))
    edit.add_argument("--url")
    for name in ("list", "report", "today", "export"):
        command = commands.add_parser(name)
        command.add_argument("--project")
        command.add_argument("--status", choices=STATUSES)
        command.add_argument("--tag")
        if name in ("report", "today"):
            command.add_argument("--timezone", default="UTC", help="IANA timezone; default UTC")
        if name == "report":
            command.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD")
            command.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD")
        if name == "export":
            command.add_argument("--format", choices=("json", "csv"), required=True)
            command.add_argument("--output", type=Path, help="Create a new file; never overwrite")
    return root


def normalize(argv: list[str]) -> list[str]:
    """Move only known global options; preserve values of command options verbatim."""
    global_args: list[str] = []
    remaining: list[str] = []
    arities = {
        "--project": 1,
        "--title": 1,
        "--tag": 1,
        "--description": 1,
        "--alias": 1,
        "--metadata": 1,
        "--notes": 1,
        "--status": 1,
        "--ref": 2,
        "--url": 1,
        "--timezone": 1,
        "--from": 1,
        "--to": 1,
        "--format": 1,
        "--output": 1,
    }
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            remaining.extend(argv[i:])
            break
        if arg == "--json" or arg.startswith("--db="):
            global_args.append(arg)
        elif arg == "--db":
            if i + 1 >= len(argv):
                raise WorklogError("--db requires a path", "usage_error")
            global_args.extend(argv[i : i + 2])
            i += 1
        else:
            remaining.append(arg)
            count = arities.get(arg, 0)
            remaining.extend(argv[i + 1 : i + 1 + count])
            i += count
        i += 1
    return global_args + remaining


def dispatch(args: argparse.Namespace, ledger: Ledger) -> Any:
    command = args.command
    if command == "project":
        try:
            metadata = json.loads(args.metadata)
        except ValueError as exc:
            raise WorklogError("--metadata must be a JSON object") from exc
        if not isinstance(metadata, dict):
            raise WorklogError("--metadata must be a JSON object")
        return ledger.add_project(args.name, args.alias, metadata)
    if command == "projects":
        return ledger.projects()
    if command in ("start", "task"):
        task_id = getattr(args, "task_id", None)
        if task_id:
            if any(v is not None for v in (args.project, args.title, args.tag, args.description)):
                raise WorklogError("Use edit to change an existing task; start ID only resumes")
            return ledger.start(task_id)
        config = resolve(args.project, args.tag)
        if not config.project:
            raise WorklogError("Project required: use --project, .worklog.toml or global config")
        if command == "task":
            return ledger.add_task(
                config.project, args.title, list(config.tags), args.description or ""
            )
        return ledger.start(
            None, config.project, args.title, list(config.tags), args.description or ""
        )
    if command in ("resume", "switch"):
        return ledger.start(args.task_id, switch=command == "switch")
    if command == "stop":
        return ledger.stop()
    if command == "status":
        return ledger.status()
    if command == "show":
        return ledger.show(args.task_id)
    if command == "complete":
        return ledger.edit(args.task_id, status="done")
    if command == "edit":
        if args.url and not args.ref:
            raise WorklogError("--url requires --ref SYSTEM REFERENCE")
        reference = (args.ref[0], args.ref[1], args.url) if args.ref else None
        return ledger.edit(
            args.task_id,
            title=args.title,
            description=args.description,
            notes=args.notes,
            status=args.status,
            tags=[] if args.clear_tags else args.tag,
            reference=reference,
        )
    tasks = ledger.tasks(args.project, args.status, args.tag)
    if command in ("report", "today"):
        now = ledger.clock()
        if command == "today":
            try:
                today = now.astimezone(ZoneInfo(args.timezone)).date().isoformat()
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise WorklogError(f"Unknown timezone {args.timezone}") from exc
            return report(tasks, today, today, args.timezone, now)
        return report(tasks, args.date_from, args.date_to, args.timezone, now)
    if command == "export":
        return {"projects": ledger.projects(), "tasks": tasks, "exported_at": ledger.now()}
    return tasks


def duration(value: float) -> str:
    total = int(value)
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"


def task_summary(task: dict[str, Any]) -> str:
    return (
        f"{task['id']}  {task['project']}  [{task['status']}]  "
        f"{duration(task['duration_seconds'])}  {task['title']}"
    )


def human(command: str, data: Any) -> str:
    if command == "status":
        if not data["active"]:
            return "No active session."
        task = data["task"]
        session = task["sessions"][-1]
        return (
            f"● {task_summary(task)}\nStarted: {session['started_at']}\n"
            f"Elapsed: {duration(session['duration_seconds'])}"
        )
    if command == "stop":
        if not data["stopped"]:
            return "No active session."
        return (
            f"Stopped {data['task']['id']}\n"
            f"Session: {duration(data['session']['duration_seconds'])}\n"
            f"Total task time: {duration(data['task']['duration_seconds'])}"
        )
    if command in ("report", "today"):
        lines = [
            f"{r['task_id']}  {r['project']}  {duration(r['duration_seconds'])}  {r['title']}"
            for r in data["tasks"]
        ]
        return "\n".join(
            [*lines, f"Total: {duration(data['duration_seconds'])} ({data['timezone']})"]
        )
    if command == "projects":
        return (
            "\n".join(f"{p['name']}  aliases: {', '.join(p['aliases']) or '-'}" for p in data)
            or "No projects."
        )
    if command == "project":
        return f"Added project {data['name']}"
    if command == "list":
        return "\n".join(task_summary(t) for t in data) or "No tasks."
    if command == "show":
        lines = [
            task_summary(data),
            f"Description: {data['description']}",
            f"Notes: {data['notes']}",
            f"Tags: {', '.join(data['tags'])}",
        ]
        lines.extend(
            f"Session {s['id']}: {s['started_at']} → {s['ended_at'] or 'running'} "
            f"({duration(s['duration_seconds'])})"
            for s in data["sessions"]
        )
        lines.extend(
            f"Reference: {r['system']} {r['reference']} {r['url'] or ''}"
            for r in data["external_references"]
        )
        return "\n".join(lines)
    prefix = {
        "start": "Started",
        "resume": "Resumed",
        "switch": "Started",
        "task": "Created",
        "complete": "Completed",
        "edit": "Updated",
    }[command]
    result = f"{prefix} {task_summary(data)}"
    if command in ("start", "resume", "switch"):
        result += f"\nStarted: {data['sessions'][-1]['started_at']}"
    return result


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in raw
    ledger: Ledger | None = None
    try:
        args = parser().parse_args(normalize(raw))
        json_mode = args.json
        if args.command == "export" and args.json and args.format != "json":
            raise WorklogError("--json cannot be combined with --format csv")
        path = Path(args.db or os.environ.get("WORKLOG_DB") or default_db()).expanduser()
        ledger = Ledger(path)
        read_only = args.command in {
            "status",
            "projects",
            "list",
            "show",
            "report",
            "today",
            "export",
        }
        if read_only:
            with ledger.transaction(write=False):
                data = dispatch(args, ledger)
        else:
            data = dispatch(args, ledger)
        if args.command == "export":
            content = json_text(data) if args.format == "json" else csv_text(data["tasks"])
            if args.output:
                with args.output.open("x", encoding="utf-8", newline="") as handle:
                    handle.write(content)
            else:
                sys.stdout.write(content)
        elif json_mode:
            sys.stdout.write(json_text(data))
        else:
            print(human(args.command, data))
        return 0
    except (WorklogError, sqlite3.Error, OSError) as exc:
        code = exc.code if isinstance(exc, WorklogError) else "storage_error"
        if json_mode:
            print(
                json.dumps({"schema_version": 1, "error": {"code": code, "message": str(exc)}}),
                file=sys.stderr,
            )
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        if ledger is not None:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
