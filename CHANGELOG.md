# Changelog

This project follows Semantic Versioning. Notable changes are recorded here.

## [Unreleased]

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
