"""Transactional SQLite ledger. All timestamps are aware UTC ISO strings."""

import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from worklog_cli.errors import WorklogError

SCHEMA = """
CREATE TABLE projects (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE project_names (
 name TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id)
);
CREATE TABLE counters (day TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
 title TEXT NOT NULL CHECK(length(trim(title)) > 0), description TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('todo','in_progress','done')),
 created_at TEXT NOT NULL, completed_at TEXT, notes TEXT NOT NULL DEFAULT '',
 CHECK((status = 'done') = (completed_at IS NOT NULL))
);
CREATE TABLE sessions (
 id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 started_at TEXT NOT NULL, ended_at TEXT,
 CHECK(ended_at IS NULL OR ended_at >= started_at)
);
CREATE UNIQUE INDEX one_active_session ON sessions((1)) WHERE ended_at IS NULL;
CREATE INDEX sessions_task ON sessions(task_id);
CREATE TABLE tags (name TEXT PRIMARY KEY);
CREATE TABLE task_tags (
 task_id TEXT NOT NULL REFERENCES tasks(id), tag TEXT NOT NULL REFERENCES tags(name),
 PRIMARY KEY(task_id, tag)
);
CREATE TABLE external_references (
 task_id TEXT NOT NULL REFERENCES tasks(id), system TEXT NOT NULL,
 reference TEXT NOT NULL, url TEXT, PRIMARY KEY(task_id, system, reference)
);
"""


def utcnow() -> datetime:
    return datetime.now(UTC)


def nonempty(value: str, label: str) -> str:
    if not value.strip():
        raise WorklogError(f"{label} must not be empty")
    return value.strip()


def seconds(start: str, end: str) -> float:
    return round(
        max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()), 6
    )


class Ledger:
    def __init__(self, path: Path, clock: Callable[[], datetime] = utcnow) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive creation sets private permissions without changing existing files.
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.clock = clock
        try:
            self.db.execute("PRAGMA foreign_keys = ON")
            with self.transaction():
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if version > 1:
                    raise WorklogError("Database schema is newer than this CLI", "schema_version")
                if version == 0:
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            self.db.execute(statement)
                    self.db.execute("PRAGMA user_version = 1")
        except Exception:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def now(self) -> str:
        return self.clock().astimezone(UTC).isoformat(timespec="microseconds")

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def project_id(self, name: str) -> int:
        row = self.db.execute(
            "SELECT project_id FROM project_names WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise WorklogError(
                f"Unknown project {name!r}; run worklog project add {name}", "not_found"
            )
        return int(row[0])

    def add_project(
        self, name: str, aliases: list[str], metadata: dict[str, Any]
    ) -> dict[str, Any]:
        name = nonempty(name, "Project name")
        try:
            encoded_metadata = json.dumps(metadata, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise WorklogError("Metadata must contain finite JSON values") from exc
        names = {name, *(nonempty(alias, "Alias") for alias in aliases)}
        with self.transaction():
            if any(
                self.db.execute("SELECT 1 FROM project_names WHERE name=?", (n,)).fetchone()
                for n in names
            ):
                raise WorklogError("Project name or alias already exists", "conflict")
            cursor = self.db.execute(
                "INSERT INTO projects(name,metadata) VALUES (?,?)", (name, encoded_metadata)
            )
            self.db.executemany(
                "INSERT INTO project_names VALUES (?,?)", [(n, cursor.lastrowid) for n in names]
            )
        return next(project for project in self.projects() if project["name"] == name)

    def projects(self) -> list[dict[str, Any]]:
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "metadata": json.loads(row["metadata"]),
                "aliases": [
                    r[0]
                    for r in self.db.execute(
                        "SELECT name FROM project_names WHERE project_id=? "
                        "AND name != ? ORDER BY name",
                        (row["id"], row["name"]),
                    )
                ],
            }
            for row in self.db.execute("SELECT * FROM projects ORDER BY name")
        ]

    def active(self) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self.db.execute("SELECT * FROM sessions WHERE ended_at IS NULL").fetchone(),
        )

    def require_task(self, task_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise WorklogError(f"Unknown task {task_id}", "not_found")
        return cast(sqlite3.Row, row)

    def _tags(self, task_id: str, tags: list[str]) -> None:
        for tag in sorted({nonempty(tag, "Tag") for tag in tags}):
            self.db.execute("INSERT OR IGNORE INTO tags VALUES (?)", (tag,))
            self.db.execute("INSERT OR IGNORE INTO task_tags VALUES (?,?)", (task_id, tag))

    def _create(self, project: str, title: str, tags: list[str], description: str, now: str) -> str:
        project_id = self.project_id(project)
        title = nonempty(title, "Title")
        day = datetime.fromisoformat(now).strftime("%Y%m%d")
        self.db.execute(
            "INSERT INTO counters VALUES (?,1) ON CONFLICT(day) DO UPDATE SET value=value+1", (day,)
        )
        sequence = self.db.execute("SELECT value FROM counters WHERE day=?", (day,)).fetchone()[0]
        task_id = f"ACT-{day}-{sequence:03d}"
        self.db.execute(
            "INSERT INTO tasks(id,project_id,title,description,status,created_at) "
            "VALUES (?,?,?,?,'todo',?)",
            (task_id, project_id, title, description, now),
        )
        self._tags(task_id, tags)
        return task_id

    def add_task(
        self, project: str, title: str, tags: list[str], description: str = ""
    ) -> dict[str, Any]:
        with self.transaction():
            now = self.now()
            task_id = self._create(project, title, tags, description, now)
            return self.show(task_id, now)

    def _start(self, task_id: str, now: str) -> None:
        task = self.require_task(task_id)
        if task["status"] == "done":
            raise WorklogError("Task is complete; reopen with edit --status todo", "conflict")
        if self.active():
            raise WorklogError("A session is already active; stop it or use switch ID", "conflict")
        latest = self.db.execute("SELECT MAX(ended_at) FROM sessions").fetchone()[0]
        if latest is not None and now < latest:
            raise WorklogError(
                "Clock precedes the last session end; check system time", "clock_error"
            )
        self.db.execute("INSERT INTO sessions(task_id,started_at) VALUES (?,?)", (task_id, now))
        self.db.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (task_id,))

    def start(
        self,
        task_id: str | None,
        project: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        description: str = "",
        *,
        switch: bool = False,
    ) -> dict[str, Any]:
        with self.transaction():
            now = self.now()
            if switch:
                self._stop(now)
            if task_id is None:
                if not project or title is None:
                    raise WorklogError("A project and --title are required to create a task")
                task_id = self._create(project, title, tags or [], description, now)
            self._start(task_id, now)
            return self.show(task_id, now)

    def _stop(self, now: str) -> sqlite3.Row | None:
        active = self.active()
        if active is not None:
            if now < active["started_at"]:
                raise WorklogError("Clock precedes session start; check system time", "clock_error")
            self.db.execute("UPDATE sessions SET ended_at=? WHERE id=?", (now, active["id"]))
        return active

    def stop(self) -> dict[str, Any]:
        with self.transaction():
            now = self.now()
            active = self._stop(now)
            if active is None:
                return {"stopped": False, "task": None, "session": None}
            task = self.show(active["task_id"], now)
            return {"stopped": True, "task": task, "session": task["sessions"][-1]}

    def status(self) -> dict[str, Any]:
        active = self.active()
        return {
            "active": active is not None,
            "task": self.show(active["task_id"]) if active is not None else None,
        }

    def show(self, task_id: str, now: str | None = None) -> dict[str, Any]:
        row = self.require_task(task_id)
        now = now or self.now()
        task = dict(row)
        task["project"] = self.db.execute(
            "SELECT name FROM projects WHERE id=?", (row["project_id"],)
        ).fetchone()[0]
        task["tags"] = [
            r[0]
            for r in self.db.execute(
                "SELECT tag FROM task_tags WHERE task_id=? ORDER BY tag", (task_id,)
            )
        ]
        task["external_references"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT system,reference,url FROM external_references WHERE task_id=? "
                "ORDER BY system,reference",
                (task_id,),
            )
        ]
        task["sessions"] = [
            {**dict(s), "duration_seconds": seconds(s["started_at"], s["ended_at"] or now)}
            for s in self.db.execute(
                "SELECT * FROM sessions WHERE task_id=? ORDER BY id", (task_id,)
            )
        ]
        task["duration_seconds"] = round(sum(s["duration_seconds"] for s in task["sessions"]), 6)
        return task

    def tasks(
        self, project: str | None = None, status: str | None = None, tag: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if project:
            clauses.append("project_id=?")
            params.append(self.project_id(project))
        if status:
            clauses.append("status=?")
            params.append(status)
        if tag:
            clauses.append("id IN (SELECT task_id FROM task_tags WHERE tag=?)")
            params.append(tag)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        now = self.now()
        return [
            self.show(row[0], now)
            for row in self.db.execute(
                "SELECT id FROM tasks" + where + " ORDER BY created_at,id", params
            )
        ]

    def edit(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        notes: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        reference: tuple[str, str, str | None] | None = None,
    ) -> dict[str, Any]:
        with self.transaction():
            task = self.require_task(task_id)
            now = self.now()
            if title is not None:
                title = nonempty(title, "Title")
            if status is not None:
                if status not in {"todo", "in_progress", "done"}:
                    raise WorklogError("Invalid task status")
                active = self.active()
                if active is not None and active["task_id"] == task_id:
                    if status == "done":
                        self._stop(now)
                    elif status == "todo":
                        raise WorklogError("Stop the task before setting it to todo", "conflict")
            values = {"title": title, "description": description, "notes": notes}
            for key, value in values.items():
                if value is not None:
                    self.db.execute(f"UPDATE tasks SET {key}=? WHERE id=?", (value, task_id))
            if status is not None:
                self.db.execute(
                    "UPDATE tasks SET status=?,completed_at=? WHERE id=?",
                    (status, (task["completed_at"] or now) if status == "done" else None, task_id),
                )
            if tags is not None:
                self.db.execute("DELETE FROM task_tags WHERE task_id=?", (task_id,))
                self._tags(task_id, tags)
            if reference:
                system, ref, url = reference
                self.db.execute(
                    "INSERT INTO external_references VALUES (?,?,?,?) "
                    "ON CONFLICT(task_id,system,reference) DO UPDATE SET url=excluded.url",
                    (task_id, nonempty(system, "System"), nonempty(ref, "Reference"), url),
                )
            return self.show(task_id, now)
