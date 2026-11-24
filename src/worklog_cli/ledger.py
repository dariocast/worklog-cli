"""Transactional SQLite ledger of projects, tasks, agent sessions and intervals.

Intervals are always closed. An agent turn is stored as an interval that ends
at its last observed activity; nothing grows on its own.
"""

import hashlib
import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from worklog_cli import agents, spool
from worklog_cli.config import IGNORE, INBOX, RESERVED, Config, normalize_path
from worklog_cli.context import brief as brief_text
from worklog_cli.context import render
from worklog_cli.errors import WorklogError
from worklog_cli.timeline import Raw, per_day, spans, total_seconds
from worklog_cli.timeutil import iso, parse_iso, to_local, utcnow

SCHEMA_VERSION = 6
AGENTS = ("claude", "codex", "agy")
SOURCES = (*AGENTS, "manual")
STATUSES = ("open", "done", "dropped")  # dropped: closed without doing it
SCHEMA = """
CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE counters (day TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
 title TEXT NOT NULL CHECK(length(trim(title)) > 0), ref TEXT,
 status TEXT NOT NULL CHECK(status IN ('open','done')),
 auto_title INTEGER NOT NULL DEFAULT 0 CHECK(auto_title IN (0,1)), created_at TEXT NOT NULL,
 description TEXT, rank REAL NOT NULL DEFAULT 0, closed_at TEXT, next_step TEXT,
 waiting INTEGER NOT NULL DEFAULT 0 CHECK(waiting IN (0,1)),
 dropped INTEGER NOT NULL DEFAULT 0 CHECK(dropped IN (0,1)), original TEXT,
 review INTEGER NOT NULL DEFAULT 0 CHECK(review IN (0,1))
);
CREATE TABLE agent_sessions (
 agent TEXT NOT NULL, session_id TEXT NOT NULL, task_id TEXT REFERENCES tasks(id),
 cwd TEXT NOT NULL, ignored INTEGER NOT NULL DEFAULT 0 CHECK(ignored IN (0,1)),
 context_hash TEXT, created_at TEXT NOT NULL, title TEXT, PRIMARY KEY(agent, session_id),
 CHECK(ignored = 1 OR task_id IS NOT NULL)
);
CREATE TABLE links (
 task_id TEXT NOT NULL REFERENCES tasks(id), target TEXT NOT NULL CHECK(length(target) > 0),
 created_at TEXT NOT NULL, PRIMARY KEY(task_id, target)
);
CREATE TABLE intervals (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('claude','codex','agy','manual')),
 session_id TEXT, note TEXT, CHECK(ended_at >= started_at),
 CHECK((source = 'manual') = (session_id IS NULL))
);
CREATE INDEX intervals_task ON intervals(task_id);
CREATE INDEX intervals_session ON intervals(source, session_id, started_at);
"""
# Schema 2 -> 3: description, manual order (newest first) and closing time.
MIGRATE_3 = """
ALTER TABLE tasks ADD COLUMN description TEXT;
ALTER TABLE tasks ADD COLUMN rank REAL NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN closed_at TEXT;
UPDATE tasks SET rank = (
 SELECT count(*) FROM tasks newer WHERE (newer.created_at, newer.id) > (tasks.created_at, tasks.id)
);
"""
# Schema 3 -> 4: next step, waiting on someone else, closed without doing it, and the
# verbatim text of a multi-line capture.
MIGRATE_4 = """
ALTER TABLE tasks ADD COLUMN next_step TEXT;
ALTER TABLE tasks ADD COLUMN waiting INTEGER NOT NULL DEFAULT 0 CHECK(waiting IN (0,1));
ALTER TABLE tasks ADD COLUMN dropped INTEGER NOT NULL DEFAULT 0 CHECK(dropped IN (0,1));
ALTER TABLE tasks ADD COLUMN original TEXT;
"""
# Schema 4 -> 5: add 'agy' (Antigravity CLI) to the source check constraint.
MIGRATE_5 = """
PRAGMA foreign_keys=off;
CREATE TABLE intervals_new (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('claude','codex','agy','manual')),
 session_id TEXT, note TEXT, CHECK(ended_at >= started_at),
 CHECK((source = 'manual') = (session_id IS NULL))
);
INSERT INTO intervals_new SELECT * FROM intervals;
DROP TABLE intervals;
ALTER TABLE intervals_new RENAME TO intervals;
CREATE INDEX intervals_task ON intervals(task_id);
CREATE INDEX intervals_session ON intervals(source, session_id, started_at);
PRAGMA foreign_keys=on;
"""
# Schema 5 -> 6: delivered but not done (review), chat titles and links to documents.
MIGRATE_6 = """
ALTER TABLE tasks ADD COLUMN review INTEGER NOT NULL DEFAULT 0 CHECK(review IN (0,1));
ALTER TABLE agent_sessions ADD COLUMN title TEXT;
CREATE TABLE links (
 task_id TEXT NOT NULL REFERENCES tasks(id), target TEXT NOT NULL CHECK(length(target) > 0),
 created_at TEXT NOT NULL, PRIMARY KEY(task_id, target)
);
"""
MIGRATIONS = {2: MIGRATE_3, 3: MIGRATE_4, 4: MIGRATE_5, 5: MIGRATE_6}  # from version -> statements
URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
MIN_GAP = 1e-6


def parse_session(key: str) -> tuple[str, str]:
    agent, _, session_id = key.partition(":")
    if agent not in AGENTS or not session_id:
        raise WorklogError(f"Invalid session {key!r}; expected AGENT:ID, for example claude:ab12")
    return agent, session_id


def split_capture(text: str) -> tuple[str, str | None]:
    """A capture's first line is its title; a longer capture is also kept verbatim."""
    text = text.strip()
    first, _, rest = text.partition("\n")
    return first.strip(), text if rest.strip() else None


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
                if version < SCHEMA_VERSION:
                    steps = (
                        [SCHEMA]
                        if version == 0
                        else [MIGRATIONS[v] for v in range(version, SCHEMA_VERSION)]
                    )
                    for statement in ";".join(steps).split(";"):
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

    def _new_task(
        self,
        project: str,
        title: str,
        ref: str | None,
        *,
        auto: bool,
        description: str | None = None,
        original: str | None = None,
    ) -> str:
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
            "INSERT INTO tasks(id, project_id, title, ref, status, auto_title, created_at, "
            "description, original, rank) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, "
            "(SELECT coalesce(min(rank), 1) - 1 FROM tasks))",
            (
                task_id,
                project_id,
                nonempty(title, "Title"),
                (ref or "").strip() or None,
                int(auto),
                iso(now),
                (description or "").strip() or None,
                original,
            ),
        )
        return task_id

    def add_task(
        self,
        project: str | None,
        title: str,
        ref: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a task without time. Without a project it is captured in the inbox.

        The first line of ``title`` is the title; a multi-line text is also kept verbatim.
        """
        project = project.strip() if project is not None else INBOX
        if project == IGNORE:
            raise WorklogError(f"{project!r} is reserved")
        title, original = split_capture(title)
        with self.transaction():
            task_id = self._new_task(
                project, title, ref, auto=False, description=description, original=original
            )
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
        ids = [r["id"] for r in rows]
        raws = self.raws(ids) if rows else []
        last: dict[str, datetime] = {}
        for raw in raws:
            last[raw.task_id] = max(last.get(raw.task_id, raw.end), raw.end)
        totals: dict[str, float] = {}
        for span in spans(raws, self.idle):
            totals[span.task_id] = totals.get(span.task_id, 0.0) + span.seconds
        links = self._links(ids)
        stall_since = self.clock() - timedelta(seconds=self.config.stall_seconds)
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
                "description": r["description"],
                "rank": r["rank"],
                "started": r["id"] in last,
                "closed_at": r["closed_at"],
                "next": r["next_step"],
                "waiting": bool(r["waiting"]),
                "dropped": bool(r["dropped"]),
                "original": r["original"],
                "review": bool(r["review"]),
                "links": links.get(r["id"], []),
                "last_activity": iso(last[r["id"]]) if r["id"] in last else None,
                # In progress or in review, with no recorded time for too long.
                "stalled": r["status"] == "open"
                and not r["waiting"]
                and r["project"] != INBOX
                and r["id"] in last
                and last[r["id"]] < stall_since,
            }
            for r in rows
        ]

    def _links(self, task_ids: list[str]) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        if not task_ids:
            return found
        for row in self.db.execute(
            f"SELECT task_id, target FROM links WHERE task_id IN ({','.join('?' * len(task_ids))}) "
            "ORDER BY created_at, rowid",
            tuple(task_ids),
        ):
            found.setdefault(row["task_id"], []).append(row["target"])
        return found

    def tasks(
        self,
        *,
        open_only: bool = False,
        project: str | None = None,
        started: bool | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if open_only:
            clauses.append("t.status = 'open'")
        if started is not None:
            exists = "EXISTS (SELECT 1 FROM intervals i WHERE i.task_id = t.id)"
            clauses.append(exists if started else f"NOT {exists}")
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
        task["chats"] = self.chats([task_id]).get(task_id, [])
        return task

    def chats(self, task_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Chats bound to each task, and chats with time on it that are bound elsewhere now."""
        if not task_ids:
            return {}
        marks = ",".join("?" * len(task_ids))
        turns = self.db.execute(
            "SELECT task_id, source, session_id, started_at, ended_at FROM intervals "
            f"WHERE session_id IS NOT NULL AND task_id IN ({marks})",
            tuple(task_ids),
        ).fetchall()
        bound = self.db.execute(
            f"SELECT agent, session_id, task_id FROM agent_sessions WHERE task_id IN ({marks})",
            tuple(task_ids),
        ).fetchall()
        pairs = {(r["task_id"], r["agent"], r["session_id"]) for r in bound}
        pairs |= {(r["task_id"], r["source"], r["session_id"]) for r in turns}
        sessions = {
            (row["agent"], row["session_id"]): row
            for agent, session_id in {(a, s) for _, a, s in pairs}
            if (row := self._session(agent, session_id)) is not None
        }
        codex = agents.codex_titles() if any(a == "codex" for a, _ in sessions) else {}
        raws: dict[tuple[str, str, str], list[Raw]] = {}
        for r in turns:
            raws.setdefault((r["task_id"], r["source"], r["session_id"]), []).append(
                Raw(r["task_id"], parse_iso(r["started_at"]), parse_iso(r["ended_at"]), False, None)
            )
        active_since = self.clock() - self.idle
        found: dict[str, list[dict[str, Any]]] = {}
        for task_id, agent, session_id in pairs:
            session = sessions.get((agent, session_id))
            own = raws.get((task_id, agent, session_id), [])
            first = min((raw.start for raw in own), default=None)
            last = max((raw.end for raw in own), default=None)
            cwd = session["cwd"] if session is not None else ""
            found.setdefault(task_id, []).append(
                {
                    "session": f"{agent}:{session_id}",
                    "agent": agent,
                    "title": (session["title"] if session is not None else None)
                    or (codex.get(session_id) if agent == "codex" else None),
                    "cwd": cwd,
                    "task_id": session["task_id"] if session is not None else None,
                    "first_activity": iso(first) if first else None,
                    "last_activity": iso(last) if last else None,
                    "duration_seconds": round(total_seconds(spans(own, self.idle)), 6),
                    "active": last is not None and last >= active_since,
                    "resume": agents.resume(agent, session_id, cwd),
                    "_created": session["created_at"] if session is not None else "",
                }
            )
        for items in found.values():
            items.sort(key=lambda c: (c["first_activity"] or "~", c["_created"], c["session"]))
            for item in items:
                del item["_created"]
        return found

    def link(self, task_id: str, target: str, *, remove: bool = False) -> dict[str, Any]:
        """Point a task to a document: a URL as given, or a local path made absolute."""
        target = nonempty(target, "Link")
        value = target if URL.match(target) else str(normalize_path(target))
        with self.transaction():
            self._require_task(task_id)
            if remove:
                deleted = self.db.execute(
                    "DELETE FROM links WHERE task_id = ? AND target = ?", (task_id, value)
                ).rowcount
                if not deleted:
                    raise WorklogError(f"{value} is not linked to {task_id}", "not_found")
            else:
                self.db.execute(
                    "INSERT OR IGNORE INTO links(task_id, target, created_at) VALUES (?, ?, ?)",
                    (task_id, value, self.now()),
                )
        return self.show(task_id)

    def brief(self, task_id: str) -> dict[str, Any]:
        """The text that starts a new chat on a task, for example a planned one."""
        return {"task_id": task_id, "text": brief_text(self.show(task_id))}

    def edit(
        self,
        task_id: str,
        *,
        title: str | None = None,
        ref: str | None = None,
        status: str | None = None,
        project: str | None = None,
        description: str | None = None,
        next_step: str | None = None,
        waiting: bool | None = None,
        review: bool | None = None,
        clear_original: bool = False,
        before: str | None = None,
        after: str | None = None,
        top: bool = False,
    ) -> dict[str, Any]:
        if sum((before is not None, after is not None, top)) > 1:
            raise WorklogError("Use only one of --before, --after and --top", "usage_error")
        with self.transaction():
            self._require_task(task_id)
            if title is not None:
                self.db.execute(
                    "UPDATE tasks SET title = ?, auto_title = 0 WHERE id = ?",
                    (nonempty(title, "Title"), task_id),
                )
            self._set_text(task_id, ref=ref, description=description, next_step=next_step)
            if status is not None:
                self._set_status(task_id, status)
            if project is not None:
                if project.strip() in RESERVED:
                    raise WorklogError(f"{project.strip()!r} is reserved; choose a project")
                self.db.execute(
                    "UPDATE tasks SET project_id = ? WHERE id = ?",
                    (self._project_id(project.strip()), task_id),
                )
            if waiting is not None:
                self._set_waiting(task_id, waiting)
            if review is not None:
                self._set_review(task_id, review)
            if clear_original:
                # The original note is never rewritten, only removed once triaged.
                self.db.execute("UPDATE tasks SET original = NULL WHERE id = ?", (task_id,))
            if top:
                self.db.execute(
                    "UPDATE tasks SET rank = (SELECT min(rank) - 1 FROM tasks) WHERE id = ?",
                    (task_id,),
                )
            elif before is not None or after is not None:
                self._place(task_id, before, after)
        return self.show(task_id)

    def _set_text(self, task_id: str, **values: str | None) -> None:
        """Set optional free-text columns; an empty value clears one, None leaves it."""
        for column, value in values.items():
            if value is not None:
                self.db.execute(
                    f"UPDATE tasks SET {column} = ? WHERE id = ?", (value.strip() or None, task_id)
                )

    def _set_status(self, task_id: str, status: str) -> None:
        """Closing (done or dropped) sets the closing time once and ends waiting and review;
        reopening clears both closing fields."""
        if status not in STATUSES:
            raise WorklogError(f"Unknown status {status!r}", "validation_error")
        closed = status != "open"
        self.db.execute(
            "UPDATE tasks SET closed_at = CASE WHEN ? THEN "
            "(CASE WHEN status = 'done' THEN closed_at ELSE ? END) END, "
            "status = ?, dropped = ?, waiting = waiting * (1 - ?), review = review * (1 - ?) "
            "WHERE id = ?",
            (
                closed,
                self.now(),
                "done" if closed else "open",
                status == "dropped",
                closed,
                closed,
                task_id,
            ),
        )

    def _set_waiting(self, task_id: str, waiting: bool) -> None:
        """Only an open task in a project can wait on someone else; it is then not in review."""
        self._check_parked(task_id, waiting, "waiting")
        self.db.execute(
            "UPDATE tasks SET waiting = ?, review = review * (1 - ?) WHERE id = ?",
            (int(waiting), int(waiting), task_id),
        )

    def _set_review(self, task_id: str, review: bool) -> None:
        """Only an open task in a project can be in review; it is then not waiting."""
        self._check_parked(task_id, review, "in review")
        self.db.execute(
            "UPDATE tasks SET review = ?, waiting = waiting * (1 - ?) WHERE id = ?",
            (int(review), int(review), task_id),
        )

    def _check_parked(self, task_id: str, value: bool, label: str) -> None:
        task = self._task_rows("WHERE t.id = ?", (task_id,))[0]
        if value and task["status"] == "done":
            raise WorklogError(f"Reopen the task before marking it as {label}")
        if value and task["project"] == INBOX:
            raise WorklogError(f"Choose a project before marking the task as {label}")

    def _place(self, task_id: str, before: str | None, after: str | None) -> None:
        """Move a task right before or after another one in the global manual order."""
        anchor = before if before is not None else after
        assert anchor is not None
        if anchor == task_id:
            raise WorklogError("A task cannot be placed relative to itself")
        for attempt in range(2):
            target = float(self._require_task(anchor)["rank"])
            operator, order = ("<", "DESC") if before is not None else (">", "ASC")
            row = self.db.execute(
                f"SELECT rank FROM tasks WHERE rank {operator} ? AND id != ? "
                f"ORDER BY rank {order} LIMIT 1",
                (target, task_id),
            ).fetchone()
            step = -1.0 if before is not None else 1.0
            neighbour = target + step if row is None else float(row[0])
            if abs(neighbour - target) >= MIN_GAP or attempt:
                rank = (target + neighbour) / 2
                break
            self._renumber()
        self.db.execute("UPDATE tasks SET rank = ? WHERE id = ?", (rank, task_id))

    def _renumber(self) -> None:
        ids = [r[0] for r in self.db.execute("SELECT id FROM tasks ORDER BY rank, created_at, id")]
        self.db.executemany(
            "UPDATE tasks SET rank = ? WHERE id = ?", [(i, t) for i, t in enumerate(ids)]
        )

    def remove_task(self, task_id: str, *, force: bool = False) -> dict[str, Any]:
        """Delete a task with its intervals. Its chats are tracked anew on their next turn."""
        with self.transaction():
            task = self.show(task_id)
            if task["duration_seconds"] > 0 and not force:
                raise WorklogError(
                    f"Task {task_id} has {task['duration_seconds']:.0f}s of recorded time; "
                    "use --force to delete it",
                    "conflict",
                )
            removed = self.db.execute("DELETE FROM intervals WHERE task_id = ?", (task_id,))
            self.db.execute("DELETE FROM agent_sessions WHERE task_id = ?", (task_id,))
            self.db.execute("DELETE FROM links WHERE task_id = ?", (task_id,))
            self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return {
            **{k: task[k] for k in ("id", "project", "title", "ref", "duration_seconds")},
            "removed_intervals": removed.rowcount,
            "released_sessions": task["sessions"],
        }

    def _drop_if_orphan(self, task_id: str | None) -> None:
        """Delete an automatically created task left without time, chats or links."""
        if task_id is None:
            return
        self.db.execute(
            "DELETE FROM tasks WHERE id = ? AND auto_title = 1 "
            "AND NOT EXISTS (SELECT 1 FROM intervals WHERE task_id = ?) "
            "AND NOT EXISTS (SELECT 1 FROM agent_sessions WHERE task_id = ?) "
            "AND NOT EXISTS (SELECT 1 FROM links WHERE task_id = ?)",
            (task_id, task_id, task_id, task_id),
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
                parent = str(event.get("parent") or "") or None
                session = self._open_session(agent, session_id, cwd, stamp, parent)
            if session["ignored"]:
                return None
            title = str(event.get("title") or "").strip() or None
            if title is not None and title != session["title"]:
                self.db.execute(
                    "UPDATE agent_sessions SET title = ? WHERE agent = ? AND session_id = ?",
                    (title, agent, session_id),
                )
            # A new turn means someone is working on it: a closed, waiting or delivered task
            # is open again.
            self.db.execute(
                "UPDATE tasks SET status = 'open', closed_at = NULL, dropped = 0, waiting = 0, "
                "review = 0 WHERE id = ? AND (status = 'done' OR waiting = 1 OR review = 1)",
                (session["task_id"],),
            )
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

    def _open_session(
        self, agent: str, session_id: str, cwd: str, stamp: str, parent: str | None = None
    ) -> sqlite3.Row:
        """A new chat gets a provisional task, or its parent's task when it is a fork."""
        target = self.config.resolve(cwd) if cwd else None
        task_id = None
        origin = self._session(agent, parent) if parent else None
        if target != IGNORE and origin is not None and origin["task_id"]:
            task_id = origin["task_id"]
        elif target != IGNORE:
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
            "t.auto_title, t.description, t.next_step, p.name AS project FROM agent_sessions s "
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
            "description": row["description"],
            "next": row["next_step"],
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
        description: str | None = None,
        next_step: str | None = None,
        waiting: bool | None = None,
        review: bool | None = None,
    ) -> dict[str, Any]:
        agent, session_id = parse_session(key)
        values = (task_id, title, ref, project, description, next_step, waiting, review)
        if all(v is None for v in values):
            raise WorklogError(
                "Nothing to assign; use --task, --title, --description, --next, --waiting, "
                "--review, --ref or --project"
            )
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
                if project is None:
                    self._attribute_from_directory(task_id, session["cwd"])
            if title is not None:
                self.db.execute(
                    "UPDATE tasks SET title = ?, auto_title = 0 WHERE id = ?",
                    (nonempty(title, "Title"), current),
                )
            self._set_text(current, ref=ref, description=description, next_step=next_step)
            if project is not None:
                if project in RESERVED:
                    raise WorklogError(f"{project!r} is reserved; use worklog map or ignore")
                self.db.execute(
                    "UPDATE tasks SET project_id = ? WHERE id = ?",
                    (self._project_id(project), current),
                )
            if waiting is not None:
                self._set_waiting(current, waiting)
            if review is not None:
                self._set_review(current, review)
        return self.session_info(agent, session_id)

    def _attribute_from_directory(self, task_id: str, cwd: str) -> None:
        """Move an inbox task to the project its chat's directory is mapped to, if any."""
        target = self.config.resolve(cwd) if cwd else None
        if target is None or target in RESERVED:
            return
        self.db.execute(
            "UPDATE tasks SET project_id = ? WHERE id = ? AND project_id = "
            "(SELECT id FROM projects WHERE name = ?)",
            (self._project_id(target, create=True), task_id, INBOX),
        )

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

    def current(
        self, directory: str | Path | None = None, session: str | None = None
    ) -> dict[str, Any]:
        """Return the current task and tracking state for a directory or session."""
        today = to_local(self.clock(), None).date()
        today_raws = self.raws()
        daily_totals = per_day(spans(today_raws, self.idle), None)

        def task_info(task_id: str, status_label: str, extra: dict[str, Any]) -> dict[str, Any]:
            task = self.show(task_id)
            duration_today = round(daily_totals.get((today, task_id), 0.0), 6)
            return {
                "status": status_label,
                "task_id": task_id,
                "ref": task["ref"],
                "title": task["title"],
                "project": task["project"],
                "task_status": task["status"],
                "duration_today_seconds": duration_today,
                "duration_total_seconds": task["duration_seconds"],
                **extra,
            }

        # 1. If explicit session is given
        if session:
            agent, session_id = parse_session(session)
            info = self.session_info(agent, session_id)
            if info["ignored"]:
                return {"status": "ignored", "session": session, "cwd": info["cwd"]}
            if info["task_id"]:
                return task_info(
                    info["task_id"], "tracked", {"session": session, "cwd": info["cwd"]}
                )

        # 2. Match directory against agent_sessions and config
        base = normalize_path(directory or Path.cwd())

        # Check agent_sessions ordered by latest activity in intervals, then created_at.
        rows = self.db.execute(
            "SELECT s.agent, s.session_id, s.cwd, s.task_id, s.ignored, s.created_at, "
            "coalesce("
            "  (SELECT max(i.ended_at) FROM intervals i WHERE i.session_id = s.session_id), "
            "  s.created_at"
            ") AS last_active "
            "FROM agent_sessions s "
            "ORDER BY last_active DESC"
        ).fetchall()

        # Phase A: Exact match first (c == base)
        for r in rows:
            if not r["cwd"]:
                continue
            c = normalize_path(r["cwd"])
            if c == base:
                if r["ignored"]:
                    return {"status": "ignored", "cwd": str(base)}
                if r["task_id"]:
                    return task_info(
                        r["task_id"],
                        "tracked",
                        {"session": f"{r['agent']}:{r['session_id']}", "cwd": str(base)},
                    )

        # Phase B: Base is inside a project directory (c in base.parents)
        for r in rows:
            if not r["cwd"]:
                continue
            c = normalize_path(r["cwd"])
            if c in base.parents:
                if r["ignored"]:
                    return {"status": "ignored", "cwd": str(base)}
                if r["task_id"]:
                    return task_info(
                        r["task_id"],
                        "tracked",
                        {"session": f"{r['agent']}:{r['session_id']}", "cwd": str(base)},
                    )

        # 3. Check config mapping for directory
        target = self.config.resolve(str(base))
        if target == IGNORE:
            return {"status": "ignored", "cwd": str(base)}
        project = target or INBOX
        open_task = self.db.execute(
            "SELECT t.id FROM tasks t JOIN projects p ON p.id = t.project_id "
            "WHERE p.name = ? AND t.status = 'open' "
            "ORDER BY t.rank ASC, t.created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        if open_task:
            return task_info(open_task[0], "project", {"cwd": str(base)})

        return {
            "status": "untracked" if target else "unmapped",
            "project": project,
            "cwd": str(base),
        }

    def drain(self) -> None:
        def replay(event: dict[str, Any]) -> None:
            try:
                self.apply_event(event)
            except WorklogError as exc:
                # Invalid events can never succeed; storage errors are retried later.
                spool.log_error(f"Dropped spooled event: {exc}")

        spool.drain(replay)
