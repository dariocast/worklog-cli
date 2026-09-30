# WorkLog CLI

A small, local task and time ledger for humans, Claude Code, Codex and other
agents. SQLite is the source of truth. No cloud, account, server, telemetry or
runtime dependencies. Python 3.11+ on macOS/Linux.

The package is **worklog-cli** and the command is `worklog`.

## Install

From this repository:

```sh
uv tool install .
# Or: pipx install .
worklog --help
```

For development (install [uv](https://docs.astral.sh/uv/) first):

```sh
uv sync --locked
uv run worklog --help
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
```

Dependencies are locked in `uv.lock`; the installed CLI uses only the Python
standard library. No publication to PyPI is assumed by these instructions.

## Quick start

```sh
worklog project add example-project --alias example
worklog start --project example --title "Review example configuration" --tag debug
worklog status
worklog stop
worklog list --json
# Copy the ID returned by start:
worklog resume ACT-20260930-001
worklog stop
worklog show ACT-20260930-001
worklog export --format csv --output times.csv
worklog export --format json --output ledger.json
```

The example ID depends on the actual UTC date and sequence. `start ID` and
`resume ID` create a new session on the same task. `start --title ...` always
creates a new task, even when another task has the same title. Save and reuse
IDs rather than matching titles. Only one session can run per database.
`start` refuses to interrupt it; `switch ID` stops and resumes atomically.
`stop` while idle succeeds without changes. Repeating `complete ID` preserves
the original completion timestamp until the task is reopened. There are no interactive prompts.

## Commands

| Command | Purpose |
| --- | --- |
| `project add NAME --alias ALIAS --metadata '{"team":"demo"}'` | Register a project |
| `projects` | List canonical project names and aliases |
| `task add --project NAME --title TITLE` | Create without starting |
| `start --project NAME --title TITLE --tag TAG` | Create and start |
| `start ID` / `resume ID` | Add a session to an unfinished task |
| `switch ID` | Atomically stop current session and resume target |
| `stop` / `status` | Stop or inspect running session |
| `list --project NAME --status in_progress --tag debug` | Filter tasks |
| `show ID` | Inspect all sessions, tags, notes and references |
| `edit ID --title TITLE --description TEXT --notes TEXT` | Replace task fields |
| `edit ID --tag debug --tag backend` / `edit ID --clear-tags` | Replace/clear tags |
| `edit ID --ref jira DEMO-123 --url https://example.org/ticket` | Add/update external reference |
| `complete ID` | Close its active session and mark done |
| `edit ID --status todo` | Reopen a completed task |
| `today --timezone Europe/Paris` | Today's time in the selected timezone |
| `report --from 2026-09-01 --to 2026-09-30 --timezone Europe/Paris` | Summarize actual intervals |
| `export --format csv` / `export --format json` | Export to stdout |

`--json` and `--db PATH` work before or after the subcommand. Report/export
support the same filters as list. Reports default to all dates and UTC;
`today` also defaults to UTC. Date ranges are inclusive calendar dates; intervals
crossing midnight or a daylight-saving boundary are clipped correctly. Active
sessions contribute elapsed time at the snapshot. Human durations are HH:MM:SS;
JSON durations are seconds without billing rounding. `in_progress` means work
has begun, and remains so after stopping; `status` indicates whether a session
is actually running. All stored timestamps and ID dates use UTC.

## Storage and configuration

On **both macOS and Linux**:

- DB: `$XDG_DATA_HOME/worklog/worklog.db`, fallback `~/.local/share/worklog/worklog.db`.
- Config: `$XDG_CONFIG_HOME/worklog/config.toml`, fallback `~/.config/worklog/config.toml`.
- DB override: `--db PATH` > `WORKLOG_DB` > default. Relative explicit paths are
  relative to the current directory; relative XDG variables are ignored.

The CLI creates the DB directory and schema automatically, but never writes a
configuration file. Newly created databases have mode 0600 and new data directories
0700. Existing permissions are preserved. Keep the DB on a local filesystem;
concurrent CLI processes are serialized by SQLite (10 second lock timeout).

Global configuration and a repository's `.worklog.toml` use the same format:

```toml
project = "example-project"
tags = ["example", "demo"]
```

Lookup: explicit flags > nearest `.worklog.toml` in current directory/parents >
global config > clear error for a missing project. Resolution is per key; tag
arrays replace instead of merge. Only the nearest repo file is used. Register
the project once with `project add`; detection never creates projects silently.
Config applies to **new tasks** only. List/report/export remain ledger-wide
unless explicitly filtered. Resume always uses the stored task's project/tags.
Unknown config keys or malformed TOML cause a clear error. Config is data only;
it cannot execute hooks or redirect storage.

## Agent integration and JSON API

See [Claude global rule](docs/claude-global-rule.md),
[Codex / shared ~/.agents setup](docs/agents.md), and
[JSON contract](docs/json-api.md). Rules are opt-in: ask the user whether to track
substantial new work, retain the task ID across follow-ups, stop at the end or
when switching work. Jira and GoodDay are references, never the source of truth.

```sh
worklog status --json
worklog list --json
worklog report --json
```

Success: `{"schema_version":1,"data":...}` on stdout, exit 0.
Error: `{"schema_version":1,"error":{"code":"...","message":"..."}}` on
stderr, exit 2. JSON is UTF-8; consumers should ignore unknown fields and treat
IDs as opaque. Human output is not an API. `--help`/`--version` remain plain text.

## Export and backup

CSV has one row per session and an empty-session row for tasks without sessions.
Tags and references are JSON cells. Spreadsheet formula prefixes are escaped
with an apostrophe; use JSON for lossless content. JSON includes projects,
aliases, metadata, tasks, sessions, tags and external references. It is a snapshot,
not yet an import/restore format. `--output` creates a new file and refuses to
overwrite one. `--json --format csv` is rejected to prevent mixed formats.

For an exact backup, use SQLite's backup facility (also safe while the CLI is
being used):

```sh
sqlite3 "$HOME/.local/share/worklog/worklog.db" '.backup /absolute/path/worklog-backup.db'
```

Adjust the source for XDG/overrides. To restore, stop using the CLI and replace
the DB with the backup. Export files may contain private notes; choose their
location carefully. Nothing sends data to corporate systems.

## Design, development and limitations

[Technical specification](docs/design.md) describes the schema and decisions.
The code separates CLI, config, transactional ledger, report calculation and
export. SQLite `user_version` is 1; newer schemas are rejected. Future versions
must add tested migrations before changing it. Tests cover concurrency,
rollback, reports, config, exports and subprocess acceptance flows.

0.1 deliberately excludes XLSX, manual session time correction, import, deletion,
billing, network integrations and multiple simultaneous timers. CSV opens in
spreadsheet apps; the export module is the extension point for future formats.
A timer measures wall-clock time, including machine sleep, until explicitly
stopped. A backward clock change raises an actionable error on stop/start;
fix the system clock before retrying. No background process guesses work time.

Contributions: see [CONTRIBUTING.md](CONTRIBUTING.md). Changes follow SemVer and
[CHANGELOG.md](CHANGELOG.md). Licensed under [MIT](LICENSE).
