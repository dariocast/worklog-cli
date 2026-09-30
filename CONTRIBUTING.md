# Contributing

Use Python 3.11+ and uv. Work from the repository root:

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv build
```

Keep the runtime dependency-free unless a concrete benefit justifies a change.
Separate task identity from session intervals; preserve the JSON v1 contract.
Add behavior-focused tests for bugs and new features. Never use your real ledger
in tests: set XDG_DATA_HOME/XDG_CONFIG_HOME and WORKLOG_DB to temporary paths.

Schema changes need a versioned, transactional migration and tests using an old
database. Document CLI/API changes in README and docs/json-api.md, and update
CHANGELOG.md. Versions in pyproject.toml and src/worklog_cli/__init__.py must agree.

Before release: run all checks, inspect the wheel/sdist, update SemVer and the
changelog, commit and tag vX.Y.Z. Publication is manual; CI has no publish token.
Submit focused pull requests with a description of behavior and verification.
Contributions are provided under the project's MIT license.
