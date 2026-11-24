# Agent integration

## Claude Code and Codex

```sh
worklog setup
```

This installs three command hooks per agent, each running
`worklog hook KIND --agent AGENT` with the absolute path of the executable:

| Hook event | Kind | Effect |
| --- | --- | --- |
| `UserPromptSubmit` | `prompt` | Starts a turn; prints a short note for the chat when it changes |
| `PostToolUse` | `activity` | Heartbeat: extends the current turn |
| `Stop` | `activity` | Ends the turn at the moment the agent stops |

The note is plain stdout on `UserPromptSubmit`, the form both agents accept.
It is printed on the first prompt of a chat and again only after something
changed (for example after `worklog map` or `worklog assign`), so it costs a
few tokens per chat. `SessionStart` is not used: Codex rejects
`additionalContext` there. Claude Code does not run `Stop` when a turn is
interrupted; the heartbeat covers that case.

Hooks read only `session_id`, `cwd` and the chat title the user set
(`session_title`, Claude Code) from the payload, never the prompt,
always exit 0, and take about 50 ms, mostly Python startup. Heartbeats only
append to the spool. If the ledger cannot be written, events go to the spool
and are recorded by the next command. Problems appear in `worklog doctor`.

What the agent is asked to do, and nothing more:

- `worklog assign --session KEY --title "..." --description "..." [--ref KEY]`
  once the activity is clear, with a one or two sentence description in the
  agent's own words;
- `worklog assign --session KEY --task ID` when the chat continues an open task
  (found with `worklog list --open --json`), for example an execution chat after
  a planning chat; put the task ID in handoffs;
- `worklog assign --session KEY --next "..."` when the next step changes (a
  handoff, a blocker, the end of a work session), with `--waiting` when the
  task now depends on someone outside the chat, or `--review` when its chat
  work is done but not delivered;
- `worklog link TASK PATH` to point the task to its spec, plan or results file;
- `worklog status --json` when asked where things stand;
- `worklog map PATH PROJECT` after asking the user, when the chat is in the
  inbox;
- `worklog ignore --session KEY` when the user says not to track the chat.

If you previously imported a WorkLog rule into `CLAUDE.md` or `AGENTS.md`,
remove it: with hooks it is redundant, and older versions asked for consent.

## Workspace rules

In a workspace where several chats share a backlog, WorkLog can replace a
hand-kept board file and inbox file. Paste this block into the workspace's
`AGENTS.md` (or `CLAUDE.md`) and remove the rules about those files:

```markdown
## Work tracking

The WorkLog board is the only board: never keep one in a file.

- Something found outside your task: `worklog add "..." --project P`, in your
  own words. Never fix it on the side.
- Where things stand: `worklog status`. Planned work starts in a new chat from
  `worklog brief ID`.
- Your chat's task: keep its next step current with
  `worklog assign --session KEY --next "..."`; add `--review` when the work is
  done but not delivered (verification, merge, release) and `--waiting` when it
  depends on someone outside the team.
- Documents of the task (spec, plan, results): `worklog link ID PATH`.
- Only the user closes a task: `worklog edit ID --done` or `--drop` when asked.
```

Existing cards are moved once, by hand or with an agent (`worklog add`,
`edit`, `link`); there is no importer.

## Other agents

Agents with a shell but without supported hooks can follow
[agent-rule.md](agent-rule.md): paste it into their global instructions. They
record one closed interval per turn with `worklog log`. This depends on the
model following instructions and is not guaranteed.

## Verify

In a mapped directory, open a new chat and ask for a small fictional task. The
chat should receive the WorkLog note, `worklog intervals --today` should show
the turn after the answer, and `worklog report --today` should list the task.
In an unmapped directory, the agent should ask for the project. In an ignored
directory, nothing is recorded.
