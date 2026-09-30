# Changelog

This project follows Semantic Versioning. Notable changes are recorded here.

## [Unreleased]

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
