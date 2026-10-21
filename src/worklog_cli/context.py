"""Instructions injected into a tracked chat. Short on purpose: it costs tokens."""

from typing import Any


def render(info: dict[str, Any]) -> str:
    session, task = info["session"], info["task_id"]
    lines = [f"[WorkLog] This chat ({session}) is tracked automatically; no timer to manage."]
    title = "provisional title" if info["provisional_title"] else f"title {info['title']!r}"
    ref = f", ref {info['ref']}" if info["ref"] else ""
    described = "described" if info["description"] else "no description"
    lines.append(f"Task {task}, project {info['project']}, {title}{ref}, {described}.")
    if info["project"] == "inbox":
        lines.append(
            f"Its time is in the inbox because {info['cwd']} is not mapped. Ask the user once"
            " which project this directory belongs to, then run "
            f"`worklog map {quote(info['cwd'])} PROJECT` (or `ignore` if it must not be tracked)."
        )
    lines += [
        "When the activity is clear, name the task in one command: "
        f'`worklog assign --session {session} --title "..." --description "..."`'
        " (description: one or two sentences, in your own words, on what is being done and why;"
        " add `--ref KEY` if a tracker key is known). If it continues an open or planned task"
        f" (`worklog list --open --json`), use `worklog assign --session {session} --task ID`.",
        'To note something for later, run `worklog add "..."` (add `--project P` if known);'
        " it does not change this chat's task.",
        "When the next step changes (a handoff, a blocker, the end of a work session), record it"
        f' in one line: `worklog assign --session {session} --next "..."`; add `--waiting` when'
        " the task now depends on someone outside this chat.",
        "Put the task ID in any handoff. If the user says not to track this chat, run "
        f"`worklog ignore --session {session}`. Do not otherwise mention WorkLog.",
    ]
    return "\n".join(lines)


def quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
