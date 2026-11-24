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
  provisional_title: boolean, created_at, duration_seconds,
  description: string|null, rank: number, started: boolean,
  closed_at: string|null, next: string|null, waiting: boolean,
  dropped: boolean, original: string|null, review: boolean, links: string[],
  last_activity: string|null, stalled: boolean}`. `rank` orders tasks manually (lower first); `started` is
  true when the task has at least one interval. `closed_at` is null for open
  tasks and for tasks closed before 0.3. `next` is the task's next step,
  `waiting` marks an open task that depends on someone else, `dropped` a
  done task closed without doing it, and `original` holds the verbatim text of
  a multi-line capture (all 0.4). `review` marks an open task whose chat work
  is done but not delivered, `links` lists paths (absolute) and URLs in the
  order added, `last_activity` is the end of its latest interval, and
  `stalled` is true for a task in progress or in review whose last activity is
  older than `stall_after` (all 0.5).
- Task detail (`show`): Task plus `intervals: Interval[]`,
  `sessions: string[]` (keys of the chats bound to it, such as `claude:ab12`)
  and `chats: Chat[]` (0.5).
- Chat: `{session, agent, title: string|null, cwd, task_id: string|null,
  first_activity: string|null, last_activity: string|null,
  duration_seconds, active: boolean, resume: string|null}`. A task's chats are
  those bound to it and those with intervals on it; `task_id` is the task the
  chat is bound to now (another one if it moved, null if ignored).
  `first_activity`, `last_activity` and `duration_seconds` cover the chat's
  intervals on this task. `active` means its last turn ended within the idle
  threshold. `resume` is a shell command that reopens the chat from its
  directory.
- Interval: `{id, task_id, project, title, started_at, ended_at,
  duration_seconds, source: "claude"|"codex"|"agy"|"manual", session: string|null,
  note: string|null}`. Intervals are always closed.
- Session: `{session, cwd, ignored, task_id, project, title, ref,
  provisional_title, description: string|null, next: string|null}`.
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
| add, task add, show, edit, link | Task (detail for show and link) |
| brief | `{task_id, text}` |
| status | `{project, done_days, counts: {inbox, todo, progress, review, waiting, done}, columns: {inbox, todo, progress, review, waiting, done: Task[]}}`; board tasks also carry `column`, `directories` and `chats` |
| list | Task[] |
| assign, move --session | Session |
| map | `{path, target, moved_sessions}` |
| inbox | `{session, cwd, task_id, duration_seconds}[]` |
| ignore | `{session, removed_intervals}` |
| task rm | `{id, project, title, ref, duration_seconds, removed_intervals, released_sessions}` |
| intervals | Interval[] |
| report | `{from, to, timezone, rounding_seconds, entries: ReportEntry[], duration_seconds}` |
| projects | `{name, tasks, open}[]` |
| project add | `{name}` |
| project rename | `{old, name, paths}`; `paths` lists the mappings moved to the new name |
| setup | `{applied: string[], changes: {agent, file, diff}[], cancelled, dry_run}` |
| doctor | `{ok, checks: {name, ok, detail}[]}` |

Inbox captures (tasks without time and without a project) are listed by
`list --project inbox --unstarted`; `inbox` keeps listing unassigned time.
`board` serves a local page and has no JSON output; its HTTP API uses the same
envelope and is internal to the page.

`report --format json` emits the same envelope as `report --json`; `--format
csv` writes CSV (`day, key, project, ref, task_id, title, hours, minutes,
notes`) with spreadsheet formula prefixes escaped.

Within v1, consumers must tolerate additional fields and treat IDs as opaque.
Removed or renamed fields require a schema_version increment.
