# Codex and shared agent instructions

WorkLog CLI works without any agent-specific directory. The ready-to-copy rule in
[claude-global-rule.md](claude-global-rule.md) applies to Claude, Codex and other
agents: copy its Markdown block into `AGENTS.md` for Codex.

## Optional integration with ~/.agents

For a setup that already centralizes instructions in `~/.agents`, keep a single
copy at `~/.agents/rules/worklog.md`. This is a suggested path, not a directory
WorkLog CLI requires or reads.

1. Copy the rule block there using your normal instruction management process.
2. In your global Claude instructions, import `@~/.agents/rules/worklog.md` if
   your Claude setup supports imports, or paste the block directly.
3. In your Codex global `AGENTS.md`, paste the block or explicitly instruct the
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
