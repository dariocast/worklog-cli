# Project instructions

WorkLog CLI is a dependency-free Python CLI. Work from this repository root.
Read docs/design.md before changing ledger or hook semantics and
docs/json-api.md before changing output. Intervals are always closed; never
introduce state that grows without new events. Mutations must be transactional.
Hooks must never fail, block, or read prompt text. Never send ledger data to
external services, and never modify users' agent settings outside `worklog setup`.

Use `uv sync --locked`, `uv run pytest`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, and `uv build` for verification.
Tests and manual validation must use temporary HOME, XDG and agent config
directories (see tests/conftest.py), never the real ledger or settings.

Public examples and test fixtures must be fictional. Never copy user work details,
client names, internal project names, ticket IDs or private tags into tracked files.
