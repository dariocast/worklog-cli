# Changelog

This project follows Semantic Versioning. Notable changes are recorded here.

## [Unreleased]

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
