# WorkLog CLI

Billable working time from your AI agent chats and manual entries, in a local
ledger, ready to copy into Jira, GoodDay or any other tracker (or to use on its
own).

- **Automatic for agents.** Hooks in Claude Code and Codex record every turn.
  The model does not have to remember anything, and there is no timer to stop.
- **Simple for humans.** `worklog log 1h30 ...` after the fact. Nothing can be
  left running.
- **Private.** SQLite on your machine. No network, no account, no AI calls, and
  never the text of your prompts.

Python 3.11+ on macOS or Linux, no runtime dependencies.

## Install

```sh
uv tool install git+https://github.com/dariocast/worklog-cli
worklog setup
```

`setup` detects Claude Code (`~/.claude/settings.json`) and Codex
(`~/.codex/hooks.json`), shows the exact diff, and asks before writing. It keeps
a backup, leaves your other hooks alone, and can be undone with
`worklog setup --uninstall`. Run `worklog doctor` at any time to check hooks,
ledger and pending events. Upgrade with `uv tool upgrade worklog-cli`.

## Tell WorkLog where your projects are

Mappings live in your global config, never in repositories:

```sh
worklog map ~/Workspaces/ExampleClient example-client
worklog map ~/Workspaces/Personal ignore
```

The most specific path wins. Chats in an unmapped directory are tracked in the
**inbox**, and the agent asks you once which project it belongs to. That is the
only question you will get.

## How agent time is counted

Each turn runs from your prompt to the agent's last activity. A gap shorter than
the idle threshold (15 minutes by default) between turns of the same task
counts as work: reading, testing and thinking are part of the job. Longer gaps
do not count. If a turn is interrupted, it ends at the last tool the agent ran.
Two chats on the same task count once; parallel chats on different tasks each
count in full.

One chat is one task by default. The agent receives a short note in the chat
with its task ID and names the task, sets the tracker key, or attaches the chat
to an existing task (for example the execution chat of an earlier planning
chat). A chat can be excluded with `worklog ignore --session ID`.

## Everyday commands

```sh
worklog report --week                    # one row per day and task
worklog report --from 2026-09-01 --to 2026-09-30 --round 15m --format csv
worklog log 1h30 --project example-client --title "Planning meeting"
worklog log 09:00-10:30 --day yesterday --task ACT-20260930-001 --note "Review"
worklog list --open
worklog edit ACT-20260930-001 --ref DEMO-123 --done
worklog inbox
worklog intervals --today
worklog move 3f2a9c1e --task ACT-20260930-002
worklog rm 3f2a9c1e                      # prints the command to recreate it
worklog task rm ACT-20260930-003         # --force if it has recorded time
```

A report row shows the day, the tracker key (or the project when there is
none), the title, the duration and the notes:

```
2026-09-29  DEMO-123        Crash on startup  1h20  Root cause found; fix and tests
2026-09-30  example-client  CI configuration  2h10
Total 3h30
```

Days use your system timezone unless `--timezone` is given. There is no rounding
unless you ask for it, and no "already logged" state: your tracker remembers
what you logged.

## Configuration and files

```toml
# ~/.config/worklog/config.toml
idle_threshold = "15m"

[paths]
"~/Workspaces/ExampleClient" = "example-client"
"~/Workspaces/Personal" = "ignore"
```

`worklog map` rewrites this file (comments are not kept). The ledger is
`~/.local/share/worklog/ledger.db`; the hook spool and error log are in
`~/.local/state/worklog/`. XDG variables are honored, and `WORKLOG_DB` or `--db`
select another ledger. Back up with
`sqlite3 ~/.local/share/worklog/ledger.db '.backup /path/backup.db'`.

## Other agents and surfaces

Claude Code (CLI and the desktop Code tab) and Codex are supported through
hooks. Agents without hooks can follow [the fallback rule](docs/agent-rule.md)
and record closed intervals with `worklog log`; this is best effort. Regular
desktop chat, Cowork and cloud sessions cannot reach your local ledger: log that
time by hand.

## For scripts and agents

Every command accepts `--json`: `{"schema_version": 1, "data": ...}` on stdout
and exit 0, or `{"schema_version": 1, "error": {"code", "message"}}` on stderr
and exit 2. See the [JSON contract](docs/json-api.md). Human output is not an
API.

## Development

```sh
uv sync --locked
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv build
```

The [design](docs/design.md) records the decisions and their reasons. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md). MIT
licensed.
