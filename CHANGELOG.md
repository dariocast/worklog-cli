# Changelog

This project follows Semantic Versioning. Notable changes are recorded here.

## [Unreleased]

## [0.3.2] - 2026-10-15

- Board: mobile-first layout; the header scrolls away on phones and leaves
  room for focused items on wider screens.
- Board: the inbox stands out while it holds something to triage; task IDs
  move to the editor; changes show a pending state until saved.
- Board: errors stay until dismissed; empty states say what to do next.
- Board styles use a token scale for spacing, type and sizes, and honour
  reduced motion.

## [0.3.1] - 2026-10-15

Board accessibility fixes from a WCAG 2.2 audit.

- Every drag has an alternative: Alt+Up/Alt+Down on a card and Move up / Move
  down in the editor reorder tasks.
- Keyboard focus returns to the task after editing, moving, dropping and the
  periodic refresh, instead of being lost.
- A rejected drop now says why; before, the explanation never appeared.
- Card and row titles are real buttons; the Board/List switch is a pair of
  toggle buttons; the board no longer re-announces itself to screen readers.
- Higher contrast for input outlines, the Delete button and secondary text.

## [0.3.0] - 2026-10-12

Inbox captures, a small backlog and a local web board. The ledger is migrated
to schema 3 on first use; 0.2 can no longer open it, so update the installed
tool (and therefore the hooks) together.

- `worklog add "text"` notes something to do; without `--project` it goes to
  the inbox. `task add` takes the title as argument and an optional project.
- Tasks gain a description, a manual order and a closing time: `edit
  --project/--description/--before/--after/--top`, `list --unstarted/--started`.
- A new agent turn on a done task reopens it; manual logs keep it done.
- Binding a chat to an inbox capture with `assign --task` moves it to the
  project of the chat's directory when that directory is mapped.
- The note injected into chats explains how to capture work for later.
- `worklog board` serves a page on 127.0.0.1 with Board (Inbox, To do,
  In progress, Done; drag and drop) and List (time per project) views, guarded
  by a per-run token, a Host check and a strict CSP, with no network resources.
- JSON: tasks gain `description`, `rank`, `started` and `closed_at`.

## [0.2.2] - 2026-10-01

- Add `worklog task rm ID` to delete a task and its intervals, for example one
  created by a chat that was opened but never used. A task with recorded time
  requires `--force`; its chats are tracked on a new task if they continue.

## [0.2.1] - 2026-09-30

- `doctor` records spooled heartbeats before counting pending events, so normal
  agent activity is no longer reported as a failure.

## [0.2.0] - 2026-09-30

Rewritten around hooks; not compatible with 0.1 (see docs/design.md).

- Claude Code and Codex hooks record every agent turn automatically; `worklog
  setup` installs them with a reviewable diff, backup and `--uninstall`.
- Worked time joins turns separated by less than the idle threshold (15 minutes
  by default); interrupted turns end at the last tool call; no open timers.
- Directory → project mappings in the global config (`worklog map`), an inbox
  for unmapped directories and `ignore` for private ones.
- The hook injects a short note into the chat so the agent can name the task,
  set its tracker key or attach the chat to an existing task (`worklog assign`).
- Manual entries with `worklog log` (durations or clock ranges); corrections
  with `intervals`, `move` and `rm`.
- Reports per local day and task in table, CSV or JSON, with optional rounding.
- `worklog doctor`, a spool for events that could not be written, and an
  error log. Prompt text is never read or stored.
- Removed timers (`start`, `stop`, `switch`, `resume`, `status`, `complete`),
  repository `.worklog.toml`, tags, aliases, metadata, notes and exports. The
  ledger is a new `ledger.db`; a 0.1 `worklog.db` is left untouched.

## [0.1.1] - 2026-09-30

- Make the shared agent rule directly importable as active instructions.
- Require consent before analysis, debugging, implementation, or review begins;
  retain consent/refusal for follow-ups and never infer it from a visited directory.
- Clarify project selection across workspaces and activities without a repository.
- Document rule updates and manual agent acceptance checks; no enforcement hook
  or guarantee of model adherence is introduced.

- Use fictional examples and clarify that agents must never infer project defaults
  from documentation.

- Rename the project to WorkLog CLI, distribution `worklog-cli`, Python package
  `worklog_cli`; retain `worklog` as the sole command.

## [0.1.0] - 2026-09-30

- Local SQLite task/session ledger with transactional single-active-session guard.
- Projects, aliases, metadata, tags, notes and external references.
- Start/resume/switch/stop/complete, task editing, filters and timezone-aware reports.
- Versioned JSON output; CSV and JSON export.
- XDG paths, repository TOML defaults, Claude/Codex rules and optional ~/.agents setup.
- Automated tests, Ruff, mypy and GitHub Actions.
