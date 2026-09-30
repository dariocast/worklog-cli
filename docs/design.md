# WorkLog CLI 0.2 design

Status: agreed specification, not yet implemented. Supersedes the 0.1 design.

## Purpose

Record billable working time with minimal effort, so it can later be logged in
Jira, GoodDay or any other tracker, or used on its own. Agent work is captured
automatically and deterministically; work without agents is entered by hand.
The same local ledger and CLI serve both. The project is public on GitHub.

Principles:

- **Mandatory by construction.** Agent time is captured by harness hooks, not
  by instructions a model may ignore.
- **Transparent.** The only thing the user may be asked is which project a
  directory belongs to, and even that can be deferred to the inbox.
- **Never in the way.** A hook never blocks, slows down or fails a chat.
- **No open state.** The ledger contains only closed intervals. Nothing can keep
  growing after a chat is closed or forgotten.
- **Local and private.** No network, no external sync, no AI model calls, and
  never the text of prompts.

## Scope and choices

Python >=3.11, zero runtime dependencies, argparse CLI, SQLite source of truth.
MIT licensed, SemVer. Distributed from GitHub only
(`uv tool install git+https://github.com/dariocast/worklog-cli`); a PyPI name
will be chosen once the interface is stable (`worklog-cli` is taken there).
Single machine; interval UUIDs keep a future merge possible. 0.2 starts a new
database (schema 2) and never reads or modifies a 0.1 ledger.

Removed from 0.1: timers (`start`, `stop`, `switch`, `resume`, `status` as a
timer), repository `.worklog.toml`, tags, project aliases and metadata, task
description and notes, multiple external references, three-state status.

## Data model

- **project**: integer ID, unique name. The reserved project `inbox` holds time
  from unmapped directories.
- **task**: opaque ID (`ACT-YYYYMMDD-NNN`, local date), project FK, title,
  optional free-text reference (for example `DEMO-123`), status `open`/`done`,
  created_at.
- **interval**: UUID, task FK, started_at, ended_at (never null), source
  (`claude`, `codex`, `manual`), optional agent session ID, optional note.
- **agent_session**: agent + session ID → task, plus ignored flag. Several
  sessions may point to the same task (for example one chat for elaboration and
  another for execution).
- **turn**: raw hook facts per agent session (turn start, last activity, turn
  end). Intervals are derived from turns; no prompt text is ever stored.

Timestamps are UTC ISO 8601 with offset; durations are computed, never stored.
Mutations are transactional (BEGIN IMMEDIATE). Schema version via
`PRAGMA user_version`; newer versions are rejected.

Storage follows XDG with `~/.local/*` and `~/.config` fallbacks on macOS and
Linux: ledger in `$XDG_DATA_HOME/worklog/worklog.db`, configuration in
`$XDG_CONFIG_HOME/worklog/config.toml`, hook spool and error log in
`$XDG_STATE_HOME/worklog/`. `WORKLOG_DB` and `--db` override the ledger path.

## Capturing agent time

### Hooks

Supported fully: Claude Code (CLI and desktop Code tab) and Codex, which share
the events used here. Each hook runs `worklog hook EVENT --agent NAME` and reads
the event JSON (session ID, cwd, timestamps) from stdin.

| Event | Effect |
| --- | --- |
| `SessionStart` | Resolve directory → project, bind session to a task, inject context |
| `UserPromptSubmit` | Record turn start; re-inject context only if it changed |
| `PostToolUse` | Heartbeat: record last activity for the session |
| `Stop` | Record turn end |

A turn without `Stop` (interrupted, app closed, crash) ends at its last
heartbeat, or at its start if no tool ran. Time is never extended beyond the
last observed activity.

Hooks always exit 0 and must stay fast. Heartbeats only append one line to the
spool file. Any event that cannot be written to SQLite (locked database, bug) is
appended to the spool and consolidated by the next successful command. Errors
go to the local error log and are surfaced by `worklog doctor` and by reports
("N events pending or failed"). Only a missing executable loses data, which
`doctor` detects.

Chat surfaces without local hooks (regular desktop chat, Cowork, cloud
sessions) are not tracked automatically; use `worklog log`. Other agents with a
shell use the fallback rule (see Agents).

### From turns to worked time

- Each turn is the interval `[turn start, turn end]`.
- Consecutive turns on the same task whose gap is shorter than the idle
  threshold (default 15 minutes, configurable) are joined: short reading and
  thinking time counts as work. Longer gaps are not counted.
- Overlapping intervals on the **same** task are merged (counted once).
- Parallel work on **different** tasks is counted in full for each task. Daily
  totals may exceed wall-clock time by design.

Joining and merging happen at read time, so changing the threshold applies
retroactively and raw facts are kept.

### Attribution

Directory → project mapping lives only in the global configuration:

```toml
# ~/.config/worklog/config.toml
idle_threshold = "15m"

[paths]
"~/Workspaces/ExampleClient" = "example-client"
"~/Workspaces/ExampleClient/mobile-app" = "example-mobile"
"~/Workspaces/Personal" = "ignore"
```

The most specific path wins. `ignore` means never tracked. An unmapped
directory is tracked in `inbox`. `worklog map PATH PROJECT` creates the project
if needed, writes the mapping and moves that directory's inbox time to it.

By default one chat is one task. On a session's first event a task is created
with a neutral provisional title (`Claude · mobile-app · 2026-09-30 09:00`).
The agent can then rename it, attach the chat to an existing open task, or set
the reference with `worklog assign`. `worklog ignore --session` discards a
chat's time and stops tracking it.

## Agents

WorkLog calls no AI model. Semantic bookkeeping is done by the agent already
working in the chat, which has the context. It must stay cheap: at most one
command at the start and one when the activity changes, no subagents, no
extended reasoning.

For hooked agents the instructions are injected by the `SessionStart` hook and
re-injected only when something changes. Example for a mapped directory:

> This chat is tracked by WorkLog as task ACT-20260930-003 (project
> example-client) with a provisional title. Once the activity is clear, run
> `worklog assign --title "..."`. If it continues an open task
> (`worklog list --open --json`), run `worklog assign --task ID` instead. Add
> `--ref KEY` if you know the tracker key. Put the task ID in any handoff.

For the inbox the instruction is to ask which project the directory belongs to
and run `worklog map`. Ignored directories get no injection. No rule file needs
to be installed for Claude Code or Codex.

Agents without hooks use [claude-global-rule.md](claude-global-rule.md): note a
start time and record the closed interval with `worklog log` before the final
answer. This is best effort.

## CLI

All commands accept `--json` (versioned envelope, see json-api.md) and `--db`.
No interactive prompts except the `setup` confirmation.

| Command | Purpose |
| --- | --- |
| `log DURATION\|HH:MM-HH:MM\|HH:MM-now [--day D] (--task ID \| --project P --title T) [--note N]` | Record a closed interval by hand |
| `task add --project P --title T [--ref R]` | Create a task without time |
| `list [--open] [--project P]` | List tasks |
| `show ID` | Task with intervals |
| `edit ID [--title T] [--ref R] [--done \| --open]` | Change a task |
| `assign [--session S] (--task ID \| --title T) [--ref R]` | Bind a chat to a task or rename its task; session defaults to the current chat |
| `map PATH PROJECT` / `map PATH ignore` | Map a directory; moves its inbox time |
| `inbox` | Unassigned time by directory and session |
| `ignore --session [S]` | Stop tracking a chat and discard its time |
| `intervals [--today \| --from D --to D]` | List intervals with short IDs |
| `move INTERVAL\|--session S --task ID` | Reassign intervals |
| `rm INTERVAL` | Delete an interval; prints the `log` command that recreates it |
| `report [--today \| --yesterday \| --week \| --from D --to D] [--round 15m] [--format table\|csv\|json]` | Billable entries |
| `projects` | List projects |
| `setup [--yes] [--uninstall]` | Install or remove hooks |
| `doctor` | Check hooks, executable path, ledger and spool |
| `hook EVENT --agent A` | Internal, called by hooks |

## Reports

One row per local day and task: reference if set, otherwise project name;
title; duration; notes of that day's intervals joined. Timezone is the system
timezone unless `--timezone` is given. No rounding unless `--round` is given
(round to nearest). Table by default, CSV and JSON on request. Reports keep no
"already logged" state: the external tracker is the memory of what was logged.

```
29/09  DEMO-123       Crash on startup       1h20  Root cause found; fix and tests
30/09  DEMO-123       Crash on startup       0h45
30/09  example-proj   CI configuration       2h10
```

## Setup

`worklog setup` detects Claude Code and Codex, shows the exact diff of
`~/.claude/settings.json` and `~/.codex/hooks.json`, writes only after
confirmation (or `--yes`), keeps a backup, never touches other hooks, is
idempotent (its entries are recognized by the `worklog hook` command) and
writes the absolute path of the executable, because desktop apps often lack
`~/.local/bin` in PATH. `--uninstall` removes only its own entries.

## Validation plan

Deterministic clock tests for turn derivation (idle joining, same-task merge,
parallel tasks, interrupted turns, cross-midnight and DST days), spool recovery,
hook payload fixtures for both agents, setup diff/idempotence/uninstall on
temporary homes, mapping precedence, report formats and rounding, correction
commands. Subprocess acceptance flows use temporary data/config/state paths.
Ruff, mypy strict and pytest run locally and in GitHub Actions. Public examples
and fixtures stay fictional.

## Open points for implementation

- Confirm Codex hook payload fields and context injection against its docs.
- Confirm Claude Code `Stop` behavior on user interrupt.
