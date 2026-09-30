# MVP verification — 2026-09-30

Verified locally on macOS:

- 32 automated tests pass on Python 3.11, 3.13 and 3.14.
- Ruff lint and formatting checks pass.
- mypy strict passes for all seven source modules.
- Wheel and source distribution build with `uv build`.
- A wheel installed in a temporary environment exposes the `worklog` command. The acceptance flow was exercised through the installed executable:
  project add → start → status → stop → start same ID → stop → list → list JSON →
  CSV export. One task retained exactly two sessions. Repository TOML discovery
  and JSON reports also worked. All smoke-test data was temporary.

Tests cover transaction rollback, concurrent initialization, ID allocation,
competing starts, schema version rejection, database constraints and permissions,
clock rollback, completion retry, references, filters, strict JSON metadata,
config precedence/errors, output overwrite protection, CSV formula escaping,
cross-midnight intervals and both daylight-saving transitions in Europe/Paris.

The final review inspected source, packaging contents, documentation, Git ignore
rules and staged files. No personal paths, ledger databases or secrets are
included. Global agent instructions were not changed. GitHub Actions is configured
for Linux/macOS with Python 3.11/3.14; hosted CI has not run because the repository
has not been published. Name availability on PyPI/GitHub is not reserved.
