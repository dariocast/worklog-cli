# Project instructions

WorkLog CLI is a dependency-free Python CLI. Work from this repository root.
Read docs/design.md before changing ledger semantics and docs/json-api.md before
changing output. Keep task identity separate from sessions. Mutations must be
transactional, with at most one active session per database. Never silently
redirect storage based on repository TOML or send ledger data to external services.

Use `uv sync --locked`, `uv run pytest`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, and `uv build` for verification.
Use temporary DB/config directories for tests and manual validation.
See docs/agents.md for opt-in tracking rules; developing this repository does not
automatically authorize recording the user's time.

Public examples and test fixtures must be fictional. Never copy user work details,
client names, internal project names, ticket IDs or private tags into tracked files.
