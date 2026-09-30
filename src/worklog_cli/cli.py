"""Command-line interface. JSON output is a stable API; human output is not."""

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from worklog_cli import __version__, config, hooks, installer, spool
from worklog_cli.errors import WorklogError
from worklog_cli.ledger import Ledger
from worklog_cli.reporting import csv_text, report, table
from worklog_cli.timeutil import (
    human_duration,
    local_moment,
    parse_clock,
    parse_day,
    parse_duration,
    parse_iso,
    to_local,
    utcnow,
)

VALUE_OPTIONS = {
    "--db", "--title", "--project", "--task", "--ref", "--note", "--day", "--from", "--to",
    "--timezone", "--round", "--format", "--session",
}  # fmt: skip
READ_ONLY = {"list", "show", "projects", "inbox", "intervals", "report"}


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:
        raise WorklogError(message, "usage_error")


def period(command: argparse.ArgumentParser) -> None:
    group = command.add_mutually_exclusive_group()
    group.add_argument("--today", action="store_true")
    group.add_argument("--yesterday", action="store_true")
    group.add_argument("--week", action="store_true", help="Monday to Sunday of this week")
    command.add_argument("--from", dest="start", help="YYYY-MM-DD, inclusive")
    command.add_argument("--to", dest="end", help="YYYY-MM-DD, inclusive")
    command.add_argument("--timezone", help="IANA name; default: system timezone")


def parser() -> Parser:
    root = Parser(prog="worklog", description="Billable time from agent chats and manual logs.")
    root.add_argument("--version", action="version", version=f"WorkLog CLI {__version__}")
    root.add_argument("--json", action="store_true", help="Versioned JSON output (any position)")
    root.add_argument("--db", help="Ledger path (any position); overrides WORKLOG_DB")
    commands = root.add_subparsers(dest="command", required=True, parser_class=Parser)

    log = commands.add_parser("log", help="Record time by hand: 1h30, 90m, 09:00-10:30, 09:00-now")
    log.add_argument("spec")
    log.add_argument("--day", default="today", help="YYYY-MM-DD, today or yesterday")
    log.add_argument("--task")
    log.add_argument("--project")
    log.add_argument("--title")
    log.add_argument("--note")

    task = commands.add_parser("task", help="Manage tasks").add_subparsers(
        dest="action", required=True, parser_class=Parser
    )
    task_add = task.add_parser("add", help="Create a task without time")
    task_add.add_argument("--project", required=True)
    task_add.add_argument("--title", required=True)
    task_add.add_argument("--ref")

    listing = commands.add_parser("list", help="List tasks")
    listing.add_argument("--open", action="store_true")
    listing.add_argument("--project")
    commands.add_parser("show", help="Show a task and its intervals").add_argument("task_id")
    edit = commands.add_parser("edit", help="Change a task")
    edit.add_argument("task_id")
    edit.add_argument("--title")
    edit.add_argument("--ref", help="Tracker key; empty string clears it")
    status = edit.add_mutually_exclusive_group()
    status.add_argument("--done", action="store_true")
    status.add_argument("--open", action="store_true")

    assign = commands.add_parser("assign", help="Bind a chat to a task or rename its task")
    assign.add_argument("--session", required=True, help="AGENT:ID, as shown in the chat")
    assign.add_argument("--task")
    assign.add_argument("--title")
    assign.add_argument("--ref")
    assign.add_argument("--project")

    mapping = commands.add_parser("map", help="Map a directory to a project, or to ignore")
    mapping.add_argument("path")
    mapping.add_argument("target", metavar="PROJECT|ignore")
    commands.add_parser("inbox", help="Time from unmapped directories")
    ignore = commands.add_parser("ignore", help="Stop tracking a chat and discard its time")
    ignore.add_argument("--session", required=True)

    intervals = commands.add_parser("intervals", help="List raw intervals (default: today)")
    period(intervals)
    move = commands.add_parser("move", help="Move an interval, or a whole chat, to a task")
    move.add_argument("interval", nargs="?")
    move.add_argument("--session")
    move.add_argument("--task", required=True)
    commands.add_parser("rm", help="Delete an interval").add_argument("interval")

    rep = commands.add_parser("report", help="Billable entries per day and task (default: today)")
    period(rep)
    rep.add_argument("--round", help="Round each entry to the nearest step, for example 15m")
    rep.add_argument("--format", choices=("table", "csv", "json"), default="table")

    commands.add_parser("projects", help="List projects")
    project = commands.add_parser("project", help="Manage projects").add_subparsers(
        dest="action", required=True, parser_class=Parser
    )
    project.add_parser("add", help="Register a project").add_argument("name")

    setup = commands.add_parser("setup", help="Install hooks for Claude Code and Codex")
    setup.add_argument("--yes", action="store_true", help="Apply without asking")
    setup.add_argument("--dry-run", action="store_true", help="Only show the changes")
    setup.add_argument("--uninstall", action="store_true", help="Remove WorkLog hooks")
    commands.add_parser("doctor", help="Check hooks, ledger and pending events")
    return root


def normalize(argv: list[str]) -> list[str]:
    """Accept --json and --db anywhere by moving them before the subcommand."""
    global_args: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--json":
            global_args.append(item)
        elif item == "--db" and index + 1 < len(argv):
            global_args += argv[index : index + 2]
            index += 1
        elif item.startswith("--db="):
            global_args.append(item)
        elif item in VALUE_OPTIONS and index + 1 < len(argv):
            rest += argv[index : index + 2]
            index += 1
        else:
            rest.append(item)
        index += 1
    return global_args + rest


def zone(name: str | None) -> tzinfo | None:
    if name is None:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise WorklogError(f"Unknown timezone {name!r}") from exc


def days(args: argparse.Namespace, tz: tzinfo | None) -> tuple[date, date]:
    now = utcnow()
    today = to_local(now, tz).date()
    if args.start or args.end:
        if args.today or args.yesterday or args.week:
            raise WorklogError("Use either --from/--to or a shortcut", "usage_error")
        start = parse_day(args.start, now, tz) if args.start else date.min
        end = parse_day(args.end, now, tz) if args.end else today
    elif args.yesterday:
        start = end = today - timedelta(days=1)
    elif args.week:
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    else:
        start = end = today
    if end < start:
        raise WorklogError("--to must not precede --from")
    return start, end


def log_range(spec: str, day_text: str) -> tuple[datetime, datetime]:
    now = utcnow()
    day = parse_day(day_text, now, None)
    if "-" in spec:
        left, _, right = spec.partition("-")
        start = local_moment(day, parse_clock(left), None)
        if right.strip() == "now":
            if day != to_local(now, None).date():
                raise WorklogError("'now' can only be used for today")
            end = now
        else:
            end = local_moment(day, parse_clock(right), None)
        if end <= start:
            raise WorklogError("End must be after start; split intervals that cross midnight")
        return start, end
    seconds = parse_duration(spec)
    if day == to_local(now, None).date():
        return now - timedelta(seconds=seconds), now
    start = local_moment(day, time(9), None)
    return start, start + timedelta(seconds=seconds)


def doctor(db: Path) -> dict[str, Any]:
    checks = []
    for target in installer.targets():
        if not target.home.is_dir():
            checks.append({"name": f"{target.agent} hooks", "ok": True, "detail": "not installed"})
            continue
        try:
            complete, command = installer.installed(target.agent)
        except WorklogError as exc:
            checks.append({"name": f"{target.agent} hooks", "ok": False, "detail": str(exc)})
            continue
        detail = "installed" if complete else "missing; run worklog setup"
        checks.append({"name": f"{target.agent} hooks", "ok": complete, "detail": detail})
        if command:
            path = command.split(" -m ")[0].strip("'")
            reachable = os.access(path, os.X_OK)
            checks.append(
                {
                    "name": f"{target.agent} hook command",
                    "ok": reachable,
                    "detail": command if reachable else f"not executable: {path}",
                }
            )
    try:
        Ledger(db, config.load()).close()
        checks.append({"name": "ledger", "ok": True, "detail": str(db)})
    except (WorklogError, sqlite3.Error, OSError) as exc:
        checks.append({"name": "ledger", "ok": False, "detail": str(exc)})
    waiting = spool.pending()
    checks.append(
        {
            "name": "pending events",
            "ok": waiting == 0,
            "detail": f"{waiting} not yet in the ledger" if waiting else "none",
        }
    )
    errors = spool.errors()
    checks.append(
        {
            "name": "hook errors",
            "ok": not errors,
            "detail": f"{len(errors)} in {spool.error_log()}; last: {errors[-1]}"
            if errors
            else "none",
        }
    )
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def setup(args: argparse.Namespace, json_mode: bool) -> dict[str, Any]:
    changes = installer.plan(args.uninstall)
    pending = [c for c in changes if c["diff"]]
    if not args.dry_run and pending and not args.yes:
        if json_mode or not sys.stdin.isatty():
            raise WorklogError("Review with --dry-run, then apply with --yes", "usage_error")
        for change in pending:
            print(change["diff"])
        if input("Apply these changes? [y/N] ").strip().lower() not in ("y", "yes"):
            return {"applied": [], "changes": [], "cancelled": True}
    applied = [] if args.dry_run else installer.apply(pending)
    return {
        "applied": applied,
        "changes": [{"agent": c["agent"], "file": c["file"], "diff": c["diff"]} for c in pending],
        "cancelled": False,
        "dry_run": bool(args.dry_run),
    }


def dispatch(args: argparse.Namespace, ledger: Ledger) -> Any:
    command = args.command
    if command == "log":
        start, end = log_range(args.spec, args.day)
        return ledger.log(
            start, end, task_id=args.task, project=args.project, title=args.title, note=args.note
        )
    if command == "task":
        return ledger.add_task(args.project, args.title, args.ref)
    if command == "list":
        return ledger.tasks(open_only=args.open, project=args.project)
    if command == "show":
        return ledger.show(args.task_id)
    if command == "edit":
        status = "done" if args.done else "open" if args.open else None
        return ledger.edit(args.task_id, title=args.title, ref=args.ref, status=status)
    if command == "assign":
        return ledger.assign(
            args.session, task_id=args.task, title=args.title, ref=args.ref, project=args.project
        )
    if command == "map":
        target = args.target.strip()
        if not target or target == config.INBOX:
            raise WorklogError("Map to a project name or to ignore")
        settings = config.load()
        key = config.display_path(config.normalize_path(args.path))
        settings.paths[key] = target
        result = ledger.remap(args.path, target)
        config.save(settings)
        return {"path": key, "target": target, **result}
    if command == "inbox":
        return ledger.inbox()
    if command == "ignore":
        return ledger.ignore(args.session)
    if command == "intervals":
        tz = zone(args.timezone)
        first, last = days(args, tz)
        return ledger.intervals(
            local_moment(max(first, date(1970, 1, 2)), time(0), tz),
            local_moment(min(last, date(9998, 12, 30)) + timedelta(days=1), time(0), tz),
        )
    if command == "move":
        if (args.interval is None) == (args.session is None):
            raise WorklogError("Give an interval ID or --session", "usage_error")
        if args.session:
            return ledger.assign(args.session, task_id=args.task)
        return ledger.move(args.interval, args.task)
    if command == "rm":
        removed = ledger.remove(args.interval)
        began = to_local(parse_iso(removed["started_at"]), None)
        finished = to_local(parse_iso(removed["ended_at"]), None)
        recreate = (
            f"worklog log {began:%H:%M:%S}-{finished:%H:%M:%S} --day {began:%Y-%m-%d} "
            f"--task {removed['task_id']}"
        )
        if removed["note"]:
            recreate += " --note " + json.dumps(removed["note"], ensure_ascii=False)
        return {**removed, "recreate": recreate}
    if command == "report":
        tz = zone(args.timezone)
        first, last = days(args, tz)
        step = parse_duration(args.round) if args.round else None
        return report(ledger, first, last, tz, step)
    if command == "projects":
        return ledger.projects()
    if command == "project":
        return ledger.add_project(args.name)
    raise WorklogError(f"Unknown command {command}", "usage_error")


def interval_line(i: dict[str, Any]) -> str:
    start = to_local(parse_iso(i["started_at"]), None)
    end = to_local(parse_iso(i["ended_at"]), None)
    note = f"  {i['note']}" if i["note"] else ""
    worked = human_duration(i["duration_seconds"])
    return (
        f"{i['id'][:8]}  {start:%Y-%m-%d %H:%M}-{end:%H:%M}  {worked}"
        f"  {i['task_id']}  {i['source']}{note}"
    )


def task_line(t: dict[str, Any]) -> str:
    ref = f" [{t['ref']}]" if t["ref"] else ""
    return (
        f"{t['id']}  {t['status']:<4}  {t['project']}{ref}  {t['title']}  "
        f"{human_duration(t['duration_seconds'])}"
    )


def human(args: argparse.Namespace, data: Any) -> str:
    command = args.command
    if command == "log":
        return f"Logged {human_duration(data['duration_seconds'])} on {data['task_id']}"
    if command in ("task", "edit"):
        return task_line(data)
    if command == "list":
        return "\n".join(task_line(t) for t in data) or "No tasks."
    if command == "show":
        lines = [task_line(data)]
        lines += [f"  {interval_line(i)}" for i in data["intervals"]]
        lines += [f"  chat {s}" for s in data["sessions"]]
        return "\n".join(lines)
    if command in ("assign", "move") and "session" in data and "task_id" in data:
        if command == "move" and "cwd" not in data:
            return f"Moved {data['id'][:8]} to {data['task_id']}"
        return f"Chat {data['session']} → {data['task_id']} ({data['project']}): {data['title']}"
    if command == "move":
        return f"Moved {data['id'][:8]} to {data['task_id']}"
    if command == "map":
        return f"Mapped {data['path']} → {data['target']}; moved {data['moved_sessions']} chats"
    if command == "inbox":
        return (
            "\n".join(
                f"{i['session']}  {i['cwd']}  {human_duration(i['duration_seconds'])}" for i in data
            )
            or "Inbox is empty."
        )
    if command == "ignore":
        return f"Ignoring {data['session']}; removed {data['removed_intervals']} intervals"
    if command == "intervals":
        return "\n".join(interval_line(i) for i in data) or "No intervals."
    if command == "rm":
        return f"Removed {data['id'][:8]}. To restore it:\n  {data['recreate']}"
    if command == "projects":
        return (
            "\n".join(f"{p['name']}  {p['open']} open / {p['tasks']} tasks" for p in data)
            or "No projects."
        )
    if command == "project":
        return f"Added project {data['name']}"
    if command == "doctor":
        return "\n".join(
            f"{'ok  ' if c['ok'] else 'FAIL'}  {c['name']}: {c['detail']}" for c in data["checks"]
        )
    if command == "setup":
        if data["cancelled"]:
            return "Cancelled; nothing changed."
        if data["dry_run"]:
            return "\n".join(c["diff"] for c in data["changes"]) or "Hooks are up to date."
        return "\n".join(f"Updated {path}" for path in data["applied"]) or "Hooks are up to date."
    return json.dumps(data)


def envelope(data: Any) -> str:
    return json.dumps({"schema_version": 1, "data": data}, ensure_ascii=False, indent=2) + "\n"


def warn_pending(json_mode: bool) -> None:
    waiting = spool.pending()
    if waiting and not json_mode:
        print(
            f"Warning: {waiting} hook events not yet recorded; run worklog doctor", file=sys.stderr
        )


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    db = Path(os.environ.get("WORKLOG_DB") or config.default_db()).expanduser()
    if raw[:1] == ["hook"]:
        kind = raw[1] if len(raw) > 1 else ""
        agent = raw[3] if len(raw) > 3 and raw[2] == "--agent" else ""
        return hooks.main(kind, agent, db)
    json_mode = "--json" in raw
    ledger: Ledger | None = None
    try:
        args = parser().parse_args(normalize(raw))
        json_mode = args.json
        if args.db:
            db = Path(args.db).expanduser()
        if args.command == "setup":
            data: Any = setup(args, json_mode)
        elif args.command == "doctor":
            data = doctor(db)
        else:
            ledger = Ledger(db, config.load())
            ledger.drain()
            if args.command in READ_ONLY:
                with ledger.transaction(write=False):
                    data = dispatch(args, ledger)
            else:
                data = dispatch(args, ledger)
            if args.command == "report":
                warn_pending(json_mode)
        if args.command == "report" and args.format == "csv":
            sys.stdout.write(csv_text(data))
        elif json_mode or (args.command == "report" and args.format == "json"):
            sys.stdout.write(envelope(data))
        elif args.command == "report":
            print(table(data))
        else:
            print(human(args, data))
        return 0
    except (WorklogError, sqlite3.Error, OSError) as exc:
        code = exc.code if isinstance(exc, WorklogError) else "storage_error"
        if json_mode:
            error = {"schema_version": 1, "error": {"code": code, "message": str(exc)}}
            print(json.dumps(error), file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        if ledger is not None:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
