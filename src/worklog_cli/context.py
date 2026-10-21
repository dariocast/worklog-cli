"""Text for agent chats: the note injected into a tracked chat (short on purpose: it costs
tokens), and the brief that starts a new chat on a planned task."""

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


def brief(task: dict[str, Any]) -> str:
    """A ready-to-paste request to work on a task in a new chat."""
    lines = [f"Work on WorkLog task {task['id']}: {task['title']}"]
    facts = [f"project {task['project']}"]
    if task["ref"]:
        facts.append(f"ref {task['ref']}")
    if task["duration_seconds"] > 0:
        minutes = round(task["duration_seconds"] / 60)
        facts.append(f"{minutes // 60}h{minutes % 60:02d} recorded so far")
    if task["status"] == "done":
        facts.append("closed (dropped)" if task["dropped"] else "closed")
    elif task["waiting"]:
        facts.append("was waiting on someone else")
    lines.append("(" + ", ".join(facts) + ")")
    if task["description"]:
        lines += ["", "What and why:", task["description"]]
    if task["next"]:
        lines += ["", f"Next step: {task['next']}"]
    if task["original"]:
        lines += ["", "Original note:", task["original"]]
    lines += [
        "",
        "Before starting, bind this chat to the task with the session key from the WorkLog"
        f" note: `worklog assign --session KEY --task {task['id']}`. Keep its next step"
        " current as you go.",
    ]
    return "\n".join(lines)


def quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"
