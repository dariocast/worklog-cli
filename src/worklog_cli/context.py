"""Instructions injected into a tracked chat. Short on purpose: it costs tokens."""

from typing import Any


def render(info: dict[str, Any]) -> str:
    session, task = info["session"], info["task_id"]
    lines = [f"[WorkLog] This chat ({session}) is tracked automatically; no timer to manage."]
    if info["project"] == "inbox":
        lines += [
            f"Its time is in the inbox (task {task}) because {info['cwd']} is not mapped.",
            "Ask the user once which project this directory belongs to, then run "
            f"`worklog map {quote(info['cwd'])} PROJECT` (or `ignore` if it must not be tracked).",
        ]
    else:
        title = "provisional title" if info["provisional_title"] else f"title {info['title']!r}"
        ref = f", ref {info['ref']}" if info["ref"] else ""
        lines.append(f"Task {task}, project {info['project']}, {title}{ref}.")
    lines += [
        f'When the activity is clear, run `worklog assign --session {session} --title "..."`'
        " (add `--ref KEY` if a tracker key is known). If it continues an open task"
        f" (`worklog list --open --json`), use `worklog assign --session {session} --task ID`.",
        "Put the task ID in any handoff. If the user says not to track this chat, run "
        f"`worklog ignore --session {session}`. Do not otherwise mention WorkLog.",
    ]
    return "\n".join(lines)


def quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
