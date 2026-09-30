# WorkLog CLI 0.1 design

## Scope and choices

Python >=3.11; zero runtime dependencies; argparse CLI, service/ledger layer,
SQLite source of truth. MIT licensed; SemVer starting at 0.1.0. The distribution
is `worklog-cli`, displayed as **WorkLog CLI**, with the `worklog` entry point.
The name is an explicit project choice; package-name availability is not reserved.
No cloud, daemon, hooks or automatic corporate sync.

## Schema

- projects: integer ID, unique name, JSON metadata object
- project_names: globally unique names/aliases mapped to projects
- tasks: ACT-YYYYMMDD-NNN ID, project FK, title, description, status
  (`todo`, `in_progress`, `done`), created_at, completed_at, notes
- sessions: integer ID, task FK, started_at, nullable ended_at
- tags + task_tags: normalized many-to-many association
- external_references: task FK, system, reference, optional URL
- counters: transactional daily ID sequence, date based on UTC

UTC ISO 8601 timestamps with microseconds; durations in seconds, calculated
from intervals. Foreign keys and checks enforce invariants. Partial unique
index permits only one running session per ledger. BEGIN IMMEDIATE serializes
mutations and ID allocation; failed switch rolls back both stop and start.
Versioned schema via PRAGMA user_version; reject newer versions without edits.

## CLI and semantics

`project add`, `projects`, `task add`, `start`, `resume`, `switch`, `stop`,
`status`, `list`, `show`, `edit`, `complete`, `today`, `report`, `export`.
Starting without an ID creates a task; `start ID`/`resume ID` appends a session
to an existing unfinished task. Repeating a title creates a distinct task.
Starting while active fails. `switch ID` explicitly stops then resumes atomically.
`complete ID` stops that task if active and marks done. `edit --status todo`
reopens a completed task. `stop` is idempotent if idle. No interactive prompts.

`--json` anywhere emits a v1 envelope `{schema_version, data}`. Failures use
`{schema_version, error: {code, message}}` on stderr, exit 2; success exits 0.
JSON fields are stable within v1; new fields may be added. Human output is not
an API. IDs are opaque to clients. All commands accept `--db PATH` anywhere.

## Configuration and reports

XDG_DATA_HOME/worklog/worklog.db; XDG_CONFIG_HOME/worklog/config.toml, with
~/.local/share and ~/.config fallbacks on both macOS and Linux. Relative XDG
variables are ignored. WORKLOG_DB and --db override storage; repo config
cannot redirect it. Project/tags: explicit flags > nearest ancestor
.worklog.toml > global config; lists replace instead of merging. Project must
already be registered; aliases resolve to its canonical name.

Reports accept inclusive --from/--to calendar dates in --timezone (IANA,
UTC by default). Intervals are clipped to boundaries, including running and
cross-midnight sessions. `today` uses the same UTC default. Reports group by
project and task; no invented billing or rounding policy. CSV has one row per
session (plus one empty-session row for tasks with none); JSON export includes
projects, tasks, nested sessions, tags and references. CSV neutralizes spreadsheet
formula prefixes. Export is stdout by default; --output refuses overwrites.

## Validation plan

Subprocess acceptance flows in isolated data/config paths; deterministic clock
tests for durations, reports, DST and rollback; concurrency checks; malformed
configuration and structured errors; SQLite constraints; packaging smoke test.
Ruff formatting/lint, mypy strict and pytest run locally and in GitHub Actions.
