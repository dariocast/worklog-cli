# Codex and shared agent instructions

WorkLog CLI works without any agent-specific directory. The ready-to-copy rule in
[claude-global-rule.md](claude-global-rule.md) applies to Claude, Codex and other
agents: copy the entire file into `AGENTS.md` for Codex. The file contains
active instructions, without a surrounding example block or installation guide.

## Optional integration with ~/.agents

For a setup that already centralizes instructions in `~/.agents`, keep a single
copy at `~/.agents/rules/worklog.md`. This is a suggested path, not a directory
WorkLog CLI requires or reads.

1. Copy the entire rule file there using your normal instruction management process.
2. In your global Claude instructions, import `@~/.agents/rules/worklog.md` if
   your Claude setup supports imports, or paste its contents directly.
3. In your Codex global `AGENTS.md`, paste the instructions or explicitly instruct the
   agent to read that absolute Markdown path before authorized tracking.
   Do not assume every agent implements Claude's `@` import syntax.
4. Keep each work repository's project and tag defaults in `.worklog.toml`.

This repository does not install or modify global instructions automatically.
There is no dependency on a personal home path, shell profile, specific AI
provider, MCP server, or `~/.agents` skill.

## Example agent workflow

```sh
worklog status --json
worklog projects --json
worklog start --title "Review example configuration" --json
# Save data.id in the conversation or handoff.
worklog stop --json
# Later, same activity (replace with actual saved ID):
worklog resume ACT-20260930-001 --json
worklog edit ACT-20260930-001 --notes "Root cause identified; validating fix" --json
worklog complete ACT-20260930-001 --json
worklog report --from 2026-09-30 --to 2026-09-30 --timezone Europe/Paris --json
```

There is one global active session per database. For truly independent ledgers,
use explicit `--db PATH`; this is an intentional split of the source of truth,
not an automatic per-agent behavior. Never run simultaneous timers to account
for the same human work twice. Reconcile ambiguous command outcomes by reading
status/list/show before retrying mutations.

## Install or update the shared rule

From the WorkLog CLI repository root:

```sh
mkdir -p ~/.agents/rules
cp docs/claude-global-rule.md ~/.agents/rules/worklog.md
```

Review any personal edits before replacing an existing copy. Keep this import
in `~/.claude/CLAUDE.md`, alongside your other instructions:

```markdown
@~/.agents/rules/worklog.md
```

A copied rule does not update when the CLI package is upgraded. Repeat the copy
when updating the rule, then start a new session or explicitly reload instructions.

## Verify agent behavior

In a fresh session without prior consent, request a fictional analysis or bugfix.
The expected first action is the tracking question, before investigation or edits.
Declining should allow untracked work; accepting should lead to project selection,
a status/ownership check and a successful start. Follow-ups should retain the same
decision and task. Repeat in a previously visited directory: familiarity is not
consent. Check loaded instructions if the question is skipped.

This is a manual agent acceptance check. CLI unit tests do not prove that an AI
model follows instructions. The rule is not an enforcement hook and does not
guarantee compliance. Do not treat a Markdown wording change as a verified fix for
a particular agent session until this check passes there.
