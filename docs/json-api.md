# JSON contract, schema version 1

Use `--json` on any command. Success writes one JSON document to stdout with
`schema_version: 1` and `data`, exit 0. Failure writes one document to stderr
with `schema_version: 1` and `error: {code, message}`, exit 2; stdout stays
empty. Codes: `usage_error`, `validation_error`, `not_found`, `conflict`,
`config_error`, `schema_version`, `storage_error`. Branch on codes; messages are
for humans. `--help` and `--version` stay plain text. `worklog hook` is internal
and always exits 0.

## Shapes

- Task: `{id, project, title, ref: string|null, status: "open"|"done",
  provisional_title: boolean, created_at, duration_seconds}`.
- Task detail (`show`): Task plus `intervals: Interval[]` and
  `sessions: string[]` (chat keys such as `claude:ab12`).
- Interval: `{id, task_id, project, title, started_at, ended_at,
  duration_seconds, source: "claude"|"codex"|"manual", session: string|null,
  note: string|null}`. Intervals are always closed.
- Session: `{session, cwd, ignored, task_id, project, title, ref,
  provisional_title}`.
- Report entry: `{day, task_id, key, project, ref, title, duration_seconds,
  raw_seconds, notes: string[]}`. `key` is the ref, or the project if there is
  no ref. `duration_seconds` is rounded only with `--round`.

Timestamps are ISO 8601 with an explicit offset (stored in UTC). Durations are
fractional seconds. Task durations and report entries apply the idle threshold
and same-task merging described in [design.md](design.md); interval durations
are raw.

| Command | `data` |
| --- | --- |
| log, move INTERVAL | Interval |
| rm | Interval plus `recreate`: the `worklog log` command that restores it |
| task add, show, edit | Task (detail for show) |
| list | Task[] |
| assign, move --session | Session |
| map | `{path, target, moved_sessions}` |
| inbox | `{session, cwd, task_id, duration_seconds}[]` |
| ignore | `{session, removed_intervals}` |
| intervals | Interval[] |
| report | `{from, to, timezone, rounding_seconds, entries: ReportEntry[], duration_seconds}` |
| projects | `{name, tasks, open}[]` |
| project add | `{name}` |
| setup | `{applied: string[], changes: {agent, file, diff}[], cancelled, dry_run}` |
| doctor | `{ok, checks: {name, ok, detail}[]}` |

`report --format json` emits the same envelope as `report --json`; `--format
csv` writes CSV (`day, key, project, ref, task_id, title, hours, minutes,
notes`) with spreadsheet formula prefixes escaped.

Within v1, consumers must tolerate additional fields and treat IDs as opaque.
Removed or renamed fields require a schema_version increment.
