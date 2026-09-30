"""Transactional SQLite ledger of projects, tasks, agent sessions and intervals.

Intervals are always closed. An agent turn is stored as an interval that ends
at its last observed activity; nothing grows on its own.
"""

import hashlib
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from worklog_cli import spool
from worklog_cli.config import IGNORE, INBOX, RESERVED, Config, normalize_path
from worklog_cli.context import render
from worklog_cli.errors import WorklogError
from worklog_cli.timeline import Raw, spans, total_seconds
from worklog_cli.timeutil import iso, parse_iso, to_local, utcnow

SCHEMA_VERSION = 2
AGENTS = ("claude", "codex")
SOURCES = (*AGENTS, "manual")
SCHEMA = """
CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE counters (day TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
 title TEXT NOT NULL CHECK(length(trim(title)) > 0), ref TEXT,
 status TEXT NOT NULL CHECK(status IN ('open','done')),
 auto_title INTEGER NOT NULL DEFAULT 0 CHECK(auto_title IN (0,1)), created_at TEXT NOT NULL
);
CREATE TABLE agent_sessions (
 agent TEXT NOT NULL, session_id TEXT NOT NULL, task_id TEXT REFERENCES tasks(id),
 cwd TEXT NOT NULL, ignored INTEGER NOT NULL DEFAULT 0 CHECK(ignored IN (0,1)),
 context_hash TEXT, created_at TEXT NOT NULL, PRIMARY KEY(agent, session_id),
 CHECK(ignored = 1 OR task_id IS NOT NULL)
);
CREATE TABLE intervals (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('claude','codex','manual')),
 session_id TEXT, note TEXT, CHECK(ended_at >= started_at),
 CHECK((source = 'manual') = (session_id IS NULL))
);
CREATE INDEX intervals_task ON intervals(task_id);
CREATE INDEX intervals_session ON intervals(source, session_id, started_at);
"""


def parse_session(key: str) -> tuple[str, str]:
    agent, _, session_id = key.partition(":")
    if agent not in AGENTS or not session_id:
        raise WorklogError(f"Invalid session {key!r}; expected AGENT:ID, for example claude:ab12")
    return agent, session_id


def nonempty(value: str, label: str) -> str:
    if not value.strip():
        raise WorklogError(f"{label} must not be empty")
    return value.strip()


class Ledger:
    def __init__(
        self, path: Path, config: Config | None = None, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self.config = config or Config()
        self.clock = clock
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys = ON")
            with self.transaction():
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if version == 1:
                    raise WorklogError(
                        f"{path} is a WorkLog 0.1 ledger; 0.2 uses a new database", "schema_version"
                    )
                if version > SCHEMA_VERSION:
                    raise WorklogError("Database schema is newer than this CLI", "schema_version")
                if version == 0:
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            self.db.execute(statement)
                    self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except Exception:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def now(self) -> str:
        return iso(self.clock())

    @property
    def idle(self) -> timedelta:
        return timedelta(seconds=self.config.idle_seconds)

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # Projects

    def _project_id(self, name: str, *, create: bool = False) -> int:
        row = self.db.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if row is not None:
            return int(row[0])
        if not create:
            raise WorklogError(
                f"Unknown project {name!r}; run worklog project add {name}", "not_found"
            )
        return int(self.db.execute("INSERT INTO projects(name) VALUES (?)", (name,)).lastrowid or 0)

    def add_project(self, name: str) -> dict[str, Any]:
        name = nonempty(name, "Project name")
        if name in RESERVED:
            raise WorklogError(f"{name!r} is reserved")
        with self.transaction():
            if self.db.execute("SELECT 1 FROM projects WHERE name = ?", (name,)).fetchone():
                raise WorklogError(f"Project {name!r} already exists", "conflict")
            self._project_id(name, create=True)
        return {"name": name}

    def projects(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT p.name, count(t.id) AS tasks, sum(t.status = 'open') AS open FROM projects p "
            "LEFT JOIN tasks t ON t.project_id = p.id GROUP BY p.id ORDER BY p.name"
        ).fetchall()
        return [{"name": r["name"], "tasks": r["tasks"], "open": r["open"] or 0} for r in rows]

    # Tasks

    def _new_task(self, project: str, title: str, ref: str | None, *, auto: bool) -> str:
        project_id = self._project_id(project, create=project == INBOX)
        now = self.clock()
        day = to_local(now, None).strftime("%Y%m%d")
        row = self.db.execute(
            "INSERT INTO counters(day, value) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET value = value + 1 RETURNING value",
            (day,),
        ).fetchone()
        task_id = f"ACT-{day}-{int(row[0]):03d}"
        self.db.execute(
            "INSERT INTO tasks(id, project_id, title, ref, status, auto_title, created_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?)",
            (task_id, project_id, nonempty(title, "Title"), ref or None, int(auto), iso(now)),
        )
        return task_id

    def add_task(self, project: str, title: str, ref: str | None = None) -> dict[str, Any]:
        with self.transaction():
            task_id = self._new_task(project, title, ref, auto=False)
        return self.show(task_id)

    def _require_task(self, task_id: str) -> sqlite3.Row:
        row: sqlite3.Row | None = self.db.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise WorklogError(f"Unknown task {task_id}", "not_found")
        return row

    def _task_rows(self, where: str = "", params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT t.*, p.name AS project FROM tasks t JOIN projects p ON p.id = t.project_id "
            f"{where} ORDER BY t.created_at, t.id",
            params,
        ).fetchall()

    def raws(self, task_ids: list[str] | None = None) -> list[Raw]:
        query = "SELECT task_id, started_at, ended_at, source, note FROM intervals"
        params: tuple[Any, ...] = ()
        if task_ids is not None:
            query += f" WHERE task_id IN ({','.join('?' * len(task_ids))})"
            params = tuple(task_ids)
        return [
            Raw(
                r["task_id"],
                parse_iso(r["started_at"]),
                parse_iso(r["ended_at"]),
                r["source"] == "manual",
                r["note"],
            )
            for r in self.db.execute(query, params)
        ]

    def _task_dicts(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        worked = spans(self.raws([r["id"] for r in rows]), self.idle) if rows else []
        totals: dict[str, float] = {}
        for span in worked:
            totals[span.task_id] = totals.get(span.task_id, 0.0) + span.seconds
        return [
            {
                "id": r["id"],
                "project": r["project"],
                "title": r["title"],
                "ref": r["ref"],
                "status": r["status"],
                "provisional_title": bool(r["auto_title"]),
                "created_at": r["created_at"],
                "duration_seconds": round(totals.get(r["id"], 0.0), 6),
            }
            for r in rows
        ]

    def tasks(self, *, open_only: bool = False, project: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if open_only:
            clauses.append("t.status = 'open'")
        if project is not None:
            self._project_id(project)
            clauses.append("p.name = ?")
            params.append(project)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._task_dicts(self._task_rows(where, tuple(params)))

    def show(self, task_id: str) -> dict[str, Any]:
        rows = self._task_rows("WHERE t.id = ?", (task_id,))
        if not rows:
            raise WorklogError(f"Unknown task {task_id}", "not_found")
        task = self._task_dicts(rows)[0]
        task["intervals"] = self._intervals("WHERE i.task_id = ?", (task_id,))
        task["sessions"] = [
            f"{r['agent']}:{r['session_id']}"
            for r in self.db.execute(
                "SELECT agent, session_id FROM agent_sessions WHERE task_id = ? "
                "ORDER BY created_at",
                (task_id,),
            )
        ]
        return task

    def edit(
        self, task_id: str, *, title: str | None, ref: str | None, status: str | None
    ) -> dict[str, Any]:
        with self.transaction():
            self._require_task(task_id)
            if title is not None:
                self.db.execute(
                    "UPDATE tasks SET title = ?, auto_title = 0 WHERE id = ?",
                    (nonempty(title, "Title"), task_id),
                )
            if ref is not None:
                self.db.execute(
                    "UPDATE tasks SET ref = ? WHERE id = ?", (ref.strip() or None, task_id)
                )
            if status is not None:
                self.db.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        return self.show(task_id)

    def _drop_if_orphan(self, task_id: str | None) -> None:
        """Delete an automatically created task left without time or chats."""
        if task_id is None:
            return
        self.db.execute(
            "DELETE FROM tasks WHERE id = ? AND auto_title = 1 "
            "AND NOT EXISTS (SELECT 1 FROM intervals WHERE task_id = ?) "
            "AND NOT EXISTS (SELECT 1 FROM agent_sessions WHERE task_id = ?)",
            (task_id, task_id, task_id),
        )

    # Intervals

    def _intervals(self, where: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT i.*, t.title, p.name AS project FROM intervals i "
            "JOIN tasks t ON t.id = i.task_id JOIN projects p ON p.id = t.project_id "
            f"{where} ORDER BY i.started_at, i.id",
            params,
        ).fetchall()
        return [
            {
                "id": r["id"],
                "task_id": r["task_id"],
                "project": r["project"],
                "title": r["title"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "duration_seconds": round(
                    (parse_iso(r["ended_at"]) - parse_iso(r["started_at"])).total_seconds(), 6
                ),
                "source": r["source"],
                "session": f"{r['source']}:{r['session_id']}" if r["session_id"] else None,
                "note": r["note"],
            }
            for r in rows
        ]

    def intervals(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self._intervals("WHERE i.ended_at >= ? AND i.started_at < ?", (iso(start), iso(end)))

    def _find_interval(self, prefix: str) -> str:
        prefix = prefix.strip().lower()
        if len(prefix) < 4:
            raise WorklogError("Interval ID must have at least 4 characters")
        rows = self.db.execute(
            "SELECT id FROM intervals WHERE id LIKE ? LIMIT 2", (prefix + "%",)
        ).fetchall()
        if not rows:
            raise WorklogError(f"Unknown interval {prefix}", "not_found")
        if len(rows) > 1:
            raise WorklogError(f"Interval ID {prefix} is ambiguous; use more characters")
        return str(rows[0][0])

    def log(
        self,
        start: datetime,
        end: datetime,
        *,
        task_id: str | None = None,
        project: str | None = None,
        title: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if end <= start:
            raise WorklogError("Interval end must be after its start")
        if end > self.clock() + timedelta(seconds=60):
            raise WorklogError("Interval end must not be in the future")
        with self.transaction():
            if task_id is not None:
                self._require_task(task_id)
            elif project is not None and title is not None:
                if project in RESERVED:
                    raise WorklogError(f"{project!r} is reserved")
                task_id = self._new_task(project, title, None, auto=False)
            else:
                raise WorklogError("Use --task ID, or --project and --title for a new task")
            interval_id = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO intervals(id, task_id, started_at, ended_at, source, note) "
                "VALUES (?, ?, ?, ?, 'manual', ?)",
                (interval_id, task_id, iso(start), iso(end), (note or "").strip() or None),
            )
        return self._intervals("WHERE i.id = ?", (interval_id,))[0]

    def move(self, interval: str, task_id: str) -> dict[str, Any]:
        with self.transaction():
            interval_id = self._find_interval(interval)
            self._require_task(task_id)
            old = self.db.execute(
                "SELECT task_id FROM intervals WHERE id = ?", (interval_id,)
            ).fetchone()[0]
            self.db.execute("UPDATE intervals SET task_id = ? WHERE id = ?", (task_id, interval_id))
            self._drop_if_orphan(old)
        return self._intervals("WHERE i.id = ?", (interval_id,))[0]

    def remove(self, interval: str) -> dict[str, Any]:
        with self.transaction():
            interval_id = self._find_interval(interval)
            removed = self._intervals("WHERE i.id = ?", (interval_id,))[0]
            self.db.execute("DELETE FROM intervals WHERE id = ?", (interval_id,))
            self._drop_if_orphan(removed["task_id"])
        return removed

    # Agent sessions

    def _session(self, agent: str, session_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.db.execute(
            "SELECT * FROM agent_sessions WHERE agent = ? AND session_id = ?", (agent, session_id)
        ).fetchone()
        return row

    def _require_session(self, agent: str, session_id: str) -> sqlite3.Row:
        row = self._session(agent, session_id)
        if row is None:
            raise WorklogError(f"Unknown session {agent}:{session_id}", "not_found")
        if row["ignored"]:
            raise WorklogError(f"Session {agent}:{session_id} is ignored")
        return row

    def apply_event(self, event: dict[str, Any]) -> str | None:
        """Record a hook event. Returns context to inject into the chat, if any."""
        kind, agent = event.get("event"), event.get("agent")
        session_id, stamp = str(event.get("session_id") or ""), str(event.get("ts") or "")
        if agent not in AGENTS or not session_id or not stamp:
            raise WorklogError(f"Incomplete hook event: {sorted(event)}")
        stamp = iso(parse_iso(stamp))
        if kind == "activity":
            with self.transaction():
                self._extend_turn(agent, session_id, stamp)
            return None
        if kind != "prompt":
            raise WorklogError(f"Unknown hook event {kind!r}")
        cwd = str(event.get("cwd") or "")
        with self.transaction():
            session = self._session(agent, session_id)
            if session is None:
                session = self._open_session(agent, session_id, cwd, stamp)
            if session["ignored"]:
                return None
            self.db.execute(
                "INSERT INTO intervals(id, task_id, started_at, ended_at, source, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, session["task_id"], stamp, stamp, agent, session_id),
            )
            return self._context_if_changed(agent, session_id)

    def _extend_turn(self, agent: str, session_id: str, stamp: str) -> None:
        self.db.execute(
            "UPDATE intervals SET ended_at = max(ended_at, ?) WHERE id = ("
            " SELECT id FROM intervals WHERE source = ? AND session_id = ? AND started_at <= ?"
            " ORDER BY started_at DESC LIMIT 1)",
            (stamp, agent, session_id, stamp),
        )

    def _open_session(self, agent: str, session_id: str, cwd: str, stamp: str) -> sqlite3.Row:
        target = self.config.resolve(cwd) if cwd else None
        task_id = None
        if target != IGNORE:
            project = target or INBOX
            self._project_id(project, create=True)
            local = to_local(parse_iso(stamp), None).strftime("%Y-%m-%d %H:%M")
            folder = Path(cwd).name or cwd or "unknown"
            title = f"{agent.capitalize()} · {folder} · {local}"
            task_id = self._new_task(project, title, None, auto=True)
        self.db.execute(
            "INSERT INTO agent_sessions(agent, session_id, task_id, cwd, ignored, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent, session_id, task_id, cwd, int(task_id is None), stamp),
        )
        session = self._session(agent, session_id)
        assert session is not None
        return session

    def session_info(self, agent: str, session_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT s.agent, s.session_id, s.cwd, s.ignored, t.id AS task_id, t.title, t.ref, "
            "t.auto_title, p.name AS project FROM agent_sessions s "
            "LEFT JOIN tasks t ON t.id = s.task_id LEFT JOIN projects p ON p.id = t.project_id "
            "WHERE s.agent = ? AND s.session_id = ?",
            (agent, session_id),
        ).fetchone()
        if row is None:
            raise WorklogError(f"Unknown session {agent}:{session_id}", "not_found")
        return {
            "session": f"{row['agent']}:{row['session_id']}",
            "cwd": row["cwd"],
            "ignored": bool(row["ignored"]),
            "task_id": row["task_id"],
            "project": row["project"],
            "title": row["title"],
            "ref": row["ref"],
            "provisional_title": bool(row["auto_title"]),
        }

    def _context_if_changed(self, agent: str, session_id: str) -> str | None:
        text = render(self.session_info(agent, session_id))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        session = self._session(agent, session_id)
        if session is not None and session["context_hash"] == digest:
            return None
        self.db.execute(
            "UPDATE agent_sessions SET context_hash = ? WHERE agent = ? AND session_id = ?",
            (digest, agent, session_id),
        )
        return text

    def assign(
        self,
        key: str,
        *,
        task_id: str | None = None,
        title: str | None = None,
        ref: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        agent, session_id = parse_session(key)
        if task_id is None and title is None and ref is None and project is None:
            raise WorklogError("Nothing to assign; use --task, --title, --ref or --project")
        with self.transaction():
            session = self._require_session(agent, session_id)
            current = session["task_id"]
            if task_id is not None and task_id != current:
                self._require_task(task_id)
                self.db.execute(
                    "UPDATE agent_sessions SET task_id = ? WHERE agent = ? AND session_id = ?",
                    (task_id, agent, session_id),
                )
                self.db.execute(
                    "UPDATE intervals SET task_id = ? WHERE source = ? AND session_id = ?",
                    (task_id, agent, session_id),
                )
                self._drop_if_orphan(current)
                current = task_id
            if title is not None:
                self.db.execute(
                    "UPDATE tasks SET title = ?, auto_title = 0 WHERE id = ?",
                    (nonempty(title, "Title"), current),
                )
            if ref is not None:
                self.db.execute(
                    "UPDATE tasks SET ref = ? WHERE id = ?", (ref.strip() or None, current)
                )
            if project is not None:
                if project in RESERVED:
                    raise WorklogError(f"{project!r} is reserved; use worklog map or ignore")
                self.db.execute(
                    "UPDATE tasks SET project_id = ? WHERE id = ?",
                    (self._project_id(project), current),
                )
        return self.session_info(agent, session_id)

    def ignore(self, key: str) -> dict[str, Any]:
        agent, session_id = parse_session(key)
        with self.transaction():
            removed = self._ignore(agent, session_id)
        return {"session": key, "removed_intervals": removed}

    def _ignore(self, agent: str, session_id: str) -> int:
        session = self._session(agent, session_id)
        if session is None:
            self.db.execute(
                "INSERT INTO agent_sessions(agent, session_id, cwd, ignored, created_at) "
                "VALUES (?, ?, '', 1, ?)",
                (agent, session_id, self.now()),
            )
            return 0
        removed = self.db.execute(
            "DELETE FROM intervals WHERE source = ? AND session_id = ?", (agent, session_id)
        ).rowcount
        self.db.execute(
            "UPDATE agent_sessions SET ignored = 1, task_id = NULL "
            "WHERE agent = ? AND session_id = ?",
            (agent, session_id),
        )
        self._drop_if_orphan(session["task_id"])
        return int(removed)

    def remap(self, directory: str, target: str) -> dict[str, Any]:
        """Move inbox chats under ``directory`` to ``target`` (a project or ignore)."""
        base = normalize_path(directory)
        with self.transaction():
            rows = self.db.execute(
                "SELECT s.agent, s.session_id, s.cwd, s.task_id FROM agent_sessions s "
                "JOIN tasks t ON t.id = s.task_id JOIN projects p ON p.id = t.project_id "
                "WHERE p.name = ?",
                (INBOX,),
            ).fetchall()
            matched = [
                r
                for r in rows
                if r["cwd"] and (base == (c := normalize_path(r["cwd"])) or base in c.parents)
            ]
            if target != IGNORE:
                project_id = self._project_id(target, create=True)
            for row in matched:
                if target == IGNORE:
                    self._ignore(row["agent"], row["session_id"])
                else:
                    self.db.execute(
                        "UPDATE tasks SET project_id = ? WHERE id = ?", (project_id, row["task_id"])
                    )
        return {"moved_sessions": len(matched)}

    def inbox(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT s.agent, s.session_id, s.cwd, s.task_id FROM agent_sessions s "
            "JOIN tasks t ON t.id = s.task_id JOIN projects p ON p.id = t.project_id "
            "WHERE p.name = ? ORDER BY s.cwd, s.created_at",
            (INBOX,),
        ).fetchall()
        worked = spans(self.raws([r["task_id"] for r in rows]), self.idle) if rows else []
        return [
            {
                "session": f"{r['agent']}:{r['session_id']}",
                "cwd": r["cwd"],
                "task_id": r["task_id"],
                "duration_seconds": round(
                    total_seconds(s for s in worked if s.task_id == r["task_id"]), 6
                ),
            }
            for r in rows
        ]

    def drain(self) -> None:
        def replay(event: dict[str, Any]) -> None:
            try:
                self.apply_event(event)
            except WorklogError as exc:
                # Invalid events can never succeed; storage errors are retried later.
                spool.log_error(f"Dropped spooled event: {exc}")

        spool.drain(replay)
