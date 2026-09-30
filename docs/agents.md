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

Hooks read only `session_id` and `cwd` from the payload, never the prompt,
always exit 0, and take about 50 ms, mostly Python startup. Heartbeats only
append to the spool. If the ledger cannot be written, events go to the spool
and are recorded by the next command. Problems appear in `worklog doctor`.

What the agent is asked to do, and nothing more:

- `worklog assign --session KEY --title "..." [--ref KEY]` once the activity is
  clear;
- `worklog assign --session KEY --task ID` when the chat continues an open task
  (found with `worklog list --open --json`), for example an execution chat after
  a planning chat; put the task ID in handoffs;
- `worklog map PATH PROJECT` after asking the user, when the chat is in the
  inbox;
- `worklog ignore --session KEY` when the user says not to track the chat.

If you previously imported a WorkLog rule into `CLAUDE.md` or `AGENTS.md`,
remove it: with hooks it is redundant, and older versions asked for consent.

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
