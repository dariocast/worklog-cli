# WorkLog CLI design

Status: implemented in 0.3.0 (0.2 core plus the backlog and board section).
Supersedes the 0.1 design.

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
- **agent_session**: agent + session ID → task, cwd, ignored flag and a hash of
  the last injected note. Several sessions may point to the same task (for
  example one chat for elaboration and another for execution).

An agent turn is an interval row that starts at the prompt and whose end is
moved forward by each heartbeat, so it is closed at every moment and never
grows by itself. No prompt text is ever stored.

Timestamps are UTC ISO 8601 with offset; durations are computed, never stored.
Mutations are transactional (BEGIN IMMEDIATE). Schema version via
`PRAGMA user_version`; newer versions are rejected.

Storage follows XDG with `~/.local/*` and `~/.config` fallbacks on macOS and
Linux: ledger in `$XDG_DATA_HOME/worklog/ledger.db` (a new file, so a 0.1
`worklog.db` stays untouched), configuration in
`$XDG_CONFIG_HOME/worklog/config.toml`, hook spool and error log in
`$XDG_STATE_HOME/worklog/`. `WORKLOG_DB` and `--db` override the ledger path.

## Capturing agent time

### Hooks

Supported fully: Claude Code (CLI and desktop Code tab) and Codex, which share
the events used here. Each hook runs `worklog hook KIND --agent NAME` and reads
only `session_id` and `cwd` from the event JSON on stdin; the time is the
moment the hook runs.

| Event | Kind | Effect |
| --- | --- | --- |
| `UserPromptSubmit` | `prompt` | Bind the chat to a task on first use; start a turn; inject the note if it changed |
| `PostToolUse` | `activity` | Heartbeat: extend the current turn |
| `Stop` | `activity` | End the turn |

`SessionStart` is not used: Codex rejects `additionalContext` on it
(openai/codex#45999), while plain stdout on `UserPromptSubmit` works for both.
Claude Code does not run `Stop` on user interrupt.

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
- Overlapping agent intervals on the **same** task are merged (counted once).
- Manual entries are counted exactly as entered; they are explicit statements.
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

For hooked agents the note is printed by the `prompt` hook on the first prompt
and again only when it changes. It names the chat's session key, because a
command run by the agent cannot otherwise know which chat it belongs to.
Example for a mapped directory:

> [WorkLog] This chat (claude:ab12) is tracked automatically; no timer to manage.
> Task ACT-20260930-003, project example-client, provisional title.
> When the activity is clear, run `worklog assign --session claude:ab12 --title "..."`
> (add `--ref KEY` if a tracker key is known). If it continues an open task
> (`worklog list --open --json`), use `worklog assign --session claude:ab12 --task ID`.
> Put the task ID in any handoff. ...

For the inbox the instruction is to ask which project the directory belongs to
and run `worklog map`. Ignored directories get no injection. No rule file needs
to be installed for Claude Code or Codex.

Agents without hooks use [agent-rule.md](agent-rule.md): note a
start time and record the closed interval with `worklog log` before the final
answer. This is best effort.

## CLI

All commands accept `--json` (versioned envelope, see json-api.md) and `--db`.
No interactive prompts except the `setup` confirmation.

| Command | Purpose |
| --- | --- |
| `log DURATION\|HH:MM-HH:MM\|HH:MM-now [--day D] (--task ID \| --project P --title T) [--note N]` | Record a closed interval by hand |
| `task add --project P --title T [--ref R]` | Create a task without time |
| `task rm ID [--force]` | Delete a task and its intervals; refuses recorded time without `--force`; its chats get a new task on their next turn |
| `list [--open] [--project P]` | List tasks |
| `show ID` | Task with intervals |
| `edit ID [--title T] [--ref R] [--done \| --open]` | Change a task |
| `assign --session S [--task ID] [--title T] [--ref R] [--project P]` | Bind a chat to a task, or change its task |
| `map PATH PROJECT` / `map PATH ignore` | Map a directory; moves its inbox time |
| `inbox` | Unassigned time by directory and session |
| `ignore --session S` | Stop tracking a chat and discard its time |
| `intervals [--today \| --from D --to D]` | List intervals with short IDs |
| `move INTERVAL\|--session S --task ID` | Reassign intervals |
| `rm INTERVAL` | Delete an interval; prints the `log` command that recreates it |
| `report [--today \| --yesterday \| --week \| --from D --to D] [--round 15m] [--format table\|csv\|json]` | Billable entries |
| `projects` | List projects |
| `project add NAME` | Register a project for manual logging |
| `setup [--dry-run] [--yes] [--uninstall]` | Install or remove hooks |
| `doctor` | Check hooks, executable path, ledger and spool |
| `hook KIND --agent A` | Internal, called by hooks; always exits 0 |

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

## Backlog and board (0.3)

Status: implemented in 0.3.0. Everything above stays valid; this section adds to it.

### Purpose

Capture work to do later in a few seconds, during a call or while busy with
something else, from the CLI, from an agent in any chat, or from a local web
board; then triage it and start it in a chat whose time lands on the planned
task. WorkLog stays a time ledger with a small backlog, not a project manager.

Out of scope, now and later: due dates, reminders, priorities other than manual
order, tags, assignees, subtasks, comments, attachments, sync with external
trackers.

### Model

No new entity. A backlog item is a task without intervals.

- A capture without a project goes to the reserved project `inbox`, which
  already means "not yet attributed". The inbox therefore holds two kinds of
  things to triage: captures, and time from unmapped directories.
- Board columns are derived, never stored:

| Column | Open tasks that are |
| --- | --- |
| Inbox | in project `inbox`, with or without time |
| To do | in another project, without intervals |
| In progress | in another project, with intervals |
| Done | `done` (default: closed in the last 14 days) |

Schema 3 adds three task columns:

- `description`: optional free text (what to do, why, links). Named so to avoid
  confusion with interval notes, which feed reports. It never appears in
  reports.
- `rank`: REAL, not null. Manual order, global across columns (a column shows
  its tasks in rank order). New tasks get `min(rank) - 1`, so captures appear
  at the top. Moving a task before another sets a rank between its neighbours;
  ranks are renumbered in the same transaction when the gap gets too small.
- `closed_at`: set when a task becomes `done`, cleared when it is reopened.

Migration 2 → 3 runs in one transaction on first open: add the columns, set
`rank` in `created_at` order, leave `closed_at` null for tasks already done.
A 0.2 CLI rejects a schema 3 ledger as newer.

### Transitions

Columns change only as a consequence of a project, a status or the presence of
intervals. Time is never created or deleted by the board.

| From → to | Automatic | CLI / agent | Board |
| --- | --- | --- | --- |
| new → Inbox | chat in an unmapped directory (with time) | `add T` | capture field |
| new → To do | | `add T --project P` | capture field with `@P` |
| new → In progress | chat in a mapped directory | `log ... --project P --title T` | no |
| Inbox → To do | | `edit --project P` (task without time) | drop on a project group |
| Inbox → In progress | `map PATH P` for time from that directory; `assign --task ID` from a chat in a directory mapped to P | `assign --session S --task ID --project P`; `edit --project P` on a task with time | drop on a project group (task with time); it lands in In progress |
| To do → In progress | | `assign --session S --task ID`; `log --task ID`; `move ... --task ID` | no: only time makes it in progress |
| In progress → To do | when its last interval goes (`rm`, `move`, `ignore`) | same commands | no |
| project → other project | | `edit --project P`; `assign --project P` | drop between project groups in To do; inline edit for any card |
| any project → Inbox | | no: `inbox` is reserved | no |
| open → Done | | `edit --done` | drop on Done |
| Done → open (derived column) | agent turn on a bound chat | `edit --open` | drag out of Done (reopen only, project unchanged) |
| open or done → deleted | provisional task left without time or chats | `task rm` (`--force` with time) | only tasks without time |
| order | | `edit --before ID2`, `edit --after ID2`, `edit --top` | drag within a column |

`map` writes configuration, so the board only shows the command and never runs
it. A task never goes back to the inbox: a wrong project is fixed by moving the
task to the right one. Reopening keeps the task's project; changing it is a
separate action.

When `assign --task ID` binds a chat to an inbox task without `--project`, and
the chat's directory is mapped to a project, the task moves to that project in
the same transaction. From an unmapped directory it stays in the inbox. An
explicit `--project` always wins.

### Done tasks and new time

Done means "no longer expected to receive time". A new **agent turn** on a done
task reopens it (`status = open`, `closed_at = null`) in the same transaction,
because the work is evidently not over. Heartbeats do not reopen. Manual
`log --task` on a done task keeps it done (backfilling forgotten time is
normal). To stop a chat from adding time
to a closed task, use `worklog ignore --session` or bind the chat elsewhere.

### Capture

- CLI: `worklog add "text"`, alias of `task add TITLE`. `task add` accepts the
  title as positional argument (`--title` keeps working); `--project` becomes
  optional and defaults to `inbox`; new `--description`.
- Agent: when the user says to note something for later, the agent runs
  `worklog add "..." [--project P]`. This never changes the chat's own task.
  The injected note gains one static line saying so, and tells the agent that a
  chat starting planned work can find it with
  `worklog list --open --unstarted --project P --json` and bind it with
  `assign --task ID`. The note does not list backlog items, so it does not
  change, and is not re-injected, whenever one is added.
- Board: a capture field always focused at the top; Enter adds to the inbox,
  a leading `@project ` adds to that project.

### CLI changes

| Command | Change |
| --- | --- |
| `add TITLE [--project P] [--ref R] [--description D]` | New, alias of `task add` |
| `task add [TITLE] [--title T] [--project P] [--ref R] [--description D]` | Positional title, optional project, description |
| `edit ID [--project P] [--description D] [--before ID2 \| --after ID2 \| --top]` | Change project (not to a reserved one), description, order |
| `list [--unstarted \| --started]` | Filter by presence of intervals |
| `board [--port N] [--no-open]` | Serve the board until Ctrl+C |

`edit --done` sets `closed_at`; `edit --open` clears it. `edit --project` on a
task bound to chats moves only that task; future chats from an unmapped
directory still go to the inbox until `worklog map`.

### Board

`worklog board` serves a single page on `127.0.0.1` (random free port unless
`--port`), opens it in the default browser unless `--no-open`, and runs in the
foreground until interrupted. Standard library only (`http.server`); HTML, CSS
and JavaScript are package data, written by hand, no build step, no external
resources of any kind. The look follows shadcn/ui (zinc theme, light and
dark) through a hand-written `ui.css` of tokens and component classes;
`board.css` holds only the board layout.

Interactions:

- Add from the capture field; click a card to edit title, reference,
  description and project inline.
- Drag and drop and inline edits follow the transition table above.
- Every drag has a non-drag alternative (WCAG 2.5.7): the editor changes the
  project and the status, and reorders with Move up / Move down; on a focused
  card Alt+Up and Alt+Down reorder within its column (or project group). A
  rejected drop is accepted only to explain why in a status message.
- The project field is a native `<select>`, a deliberate exception to the
  custom-listbox rule of the design kit: it is keyboard and screen-reader
  complete without extra code.
- A List view shows the same tasks as a table (project, task with reference,
  status, time), sortable by column, with a subtotal per project when sorted
  by project and a grand total. Clicking a row opens the same inline editor.
  The chosen view is remembered in the browser only.
- In progress and Inbox cards show worked time; inbox cards with time show their
  directory and the `worklog map` command that would attribute it.
- The page refreshes every 15 seconds while visible, so chats started meanwhile
  appear; each refresh drains the spool like any other command.

Security, since the page can change the ledger:

- Bind to `127.0.0.1` only; reject requests whose `Host` is not
  `127.0.0.1:PORT` or `localhost:PORT`.
- A random token per run is placed in the URL fragment (never sent by the
  browser as part of a request URL or Referer); the page sends it in an
  `X-Worklog-Token` header. API requests without it are rejected. The custom
  header also forces a CORS preflight, which the server never approves.
- `Content-Security-Policy: default-src 'self'`; no inline script.
- No request or ledger content is logged.

Implementation: the server holds no state besides the token. Each API request
opens the ledger, runs one ledger method (the same ones the CLI uses, so the
same validation and transactions) and closes it, so hooks and the CLI are never
blocked by an idle board. API responses use the JSON envelope of
[json-api.md](json-api.md).

| Method and path | Ledger operation |
| --- | --- |
| `GET /api/board` | Open tasks, recent done tasks, projects |
| `POST /api/tasks` | `add` |
| `PATCH /api/tasks/ID` | `edit` (title, ref, description, project, status, before, after, top) |
| `DELETE /api/tasks/ID` | `task rm` without force |

### JSON API

Within schema version 1 (only fields are added): Task gains `description:
string|null`, `rank: number`, `started: boolean` and `closed_at: string|null`.
`inbox --json` keeps its shape (unassigned time); captures are listed with
`list --project inbox --unstarted --json`. `board` has no JSON output.

### Validation additions

Migration from a populated schema 2 ledger; rank insertion, renumbering and
`--before` across filters; reopening a done task on an agent turn but not on a
heartbeat or manual log; derived columns at their boundaries (inbox task with
time, task whose last interval is removed); board server tests on an ephemeral
port for Host check, token, preflight rejection and each endpoint, with a
temporary ledger. The page itself is checked manually in a browser.

## Setup

`worklog setup` detects Claude Code and Codex, shows the exact diff of
`~/.claude/settings.json` and `~/.codex/hooks.json`, writes only after
confirmation (or `--yes`; `--dry-run` only shows it), keeps a backup, never touches other hooks, is
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

`log DURATION` on today ends now; on another day it starts at 09:00 local
time. Clock ranges must not cross midnight; split them.
