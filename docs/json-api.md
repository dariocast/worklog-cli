# JSON contract, schema version 1

Use `--json` on any command. Success writes one JSON document to stdout with
`schema_version: 1` and `data`. Failure writes one document to stderr with
`schema_version: 1` and `error: {code, message}`, exit 2; stdout stays empty.
Success exits 0. Error codes currently include `usage_error`, `validation_error`,
`not_found`, `conflict`, `clock_error`, `config_error`, `schema_version`,
`storage_error`. Messages are for humans; branch on codes. CLI help and version
remain text. Interruption and OS-level process termination are outside this API.

## Shapes

- Project: `{id: integer, name: string, aliases: string[], metadata: object}`.
- Session: `{id: integer, task_id: string, started_at: string, ended_at: string|null,
  duration_seconds: number}`.
- External reference: `{system: string, reference: string, url: string|null}`.
- Task: `{id: string, project_id: integer, project: string, title: string,
  description: string, status: "todo"|"in_progress"|"done", created_at: string,
  completed_at: string|null, notes: string, tags: string[],
  external_references: ExternalReference[], sessions: Session[],
  duration_seconds: number}`.

Empty description/notes are empty strings, not null. All timestamps contain an
explicit UTC offset. Duration includes open-session elapsed time at the read
snapshot. It is fractional seconds, never rounded to billing increments.

| Command | `data` |
| --- | --- |
| project add | Project |
| projects | Project[] |
| task add, start, resume, switch, show, edit, complete | Task |
| list | Task[] |
| status | `{active: boolean, task: Task|null}` |
| stop | `{stopped: boolean, task: Task|null, session: Session|null}` |
| report, today | Report below |
| export --format json | `{projects: Project[], tasks: Task[], exported_at: string}` |

Report:

```json
{
  "schema_version": 1,
  "data": {
    "from": "2026-09-30",
    "to": "2026-09-30",
    "timezone": "Europe/Paris",
    "as_of": "2026-09-30T14:00:00+00:00",
    "tasks": [
      {"task_id": "ACT-20260930-001", "project": "demo", "title": "Investigate",
       "session_count": 2, "duration_seconds": 4620.0}
    ],
    "projects": [{"project": "demo", "duration_seconds": 4620.0}],
    "duration_seconds": 4620.0
  }
}
```

`from` and `to` may be null (unbounded). Session count includes intervals with
positive overlap in the range. Zero-length sessions remain in show/export but
contribute nothing to reports. Filters select tasks before aggregating intervals.
Lists are deterministic: projects/aliases/tags/references alphabetically; tasks
by created_at then ID; sessions by numeric ID. Object key order is not contractual.

Export with `--output` writes the selected document to the new file and emits
nothing on stdout. CSV is a separate format and cannot be combined with --json.
JSON export preserves contents; CSV escapes potentially executable spreadsheet
cells with a leading apostrophe. Neither export is a SQLite backup or an import
format. A filtered export includes the full project registry.

Within v1, consumers must tolerate additional fields and treat IDs as opaque.
Removed/renamed fields or meaning changes require a schema_version increment.
CLI SemVer and database schema version are distinct from the JSON schema version.
