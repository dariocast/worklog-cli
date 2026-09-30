"""Install WorkLog hooks into Claude Code and Codex without touching other hooks."""

import difflib
import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worklog_cli.errors import WorklogError
from worklog_cli.timeutil import utcnow

EVENTS = {"UserPromptSubmit": "prompt", "PostToolUse": "activity", "Stop": "activity"}
MARKER = re.compile(r"\bhook (prompt|activity) --agent (claude|codex)\b")


@dataclass(frozen=True)
class Target:
    agent: str
    home: Path
    file: Path


def targets() -> list[Target]:
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    codex = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return [
        Target("claude", claude, claude / "settings.json"),
        Target("codex", codex, codex / "hooks.json"),
    ]


def executable() -> str:
    """Absolute command for hooks; desktop apps often lack ~/.local/bin in PATH."""
    argv0 = Path(sys.argv[0])
    if argv0.name == "worklog":
        return shlex.quote(os.path.abspath(argv0))
    found = shutil.which("worklog")
    if found:
        return shlex.quote(os.path.abspath(found))
    return f"{shlex.quote(sys.executable)} -m worklog_cli.cli"


def read(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.exists():
        return "", {}
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as exc:
        raise WorklogError(
            f"{path} is not valid JSON: {exc}; fix it first", "config_error"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise WorklogError(f"{path} has an unexpected structure", "config_error")
    return text, data


def without_worklog(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(data))
    hooks = result.get("hooks", {})
    for event in list(hooks):
        groups = []
        for group in hooks[event] if isinstance(hooks[event], list) else []:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                kept = [h for h in group["hooks"] if not MARKER.search(str(h.get("command", "")))]
                if not kept:
                    continue
                group = {**group, "hooks": kept}
            groups.append(group)
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if "hooks" in result and not hooks:
        del result["hooks"]
    return result


def with_worklog(data: dict[str, Any], agent: str, command: str) -> dict[str, Any]:
    result = without_worklog(data)
    hooks = result.setdefault("hooks", {})
    for event, kind in EVENTS.items():
        entry = {"type": "command", "command": f"{command} hook {kind} --agent {agent}"}
        hooks.setdefault(event, []).append({"hooks": [{**entry, "timeout": 10}]})
    return result


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n" if data else ""


def plan(uninstall: bool) -> list[dict[str, Any]]:
    command = executable()
    changes = []
    for target in targets():
        if not target.home.is_dir():
            continue
        old_text, data = read(target.file)
        new = without_worklog(data) if uninstall else with_worklog(data, target.agent, command)
        new_text = render(new)
        if not old_text and not new_text:
            continue
        if old_text.strip() and json.loads(old_text) == new:
            new_text = old_text
        diff = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                str(target.file),
                str(target.file),
            )
        )
        changes.append(
            {"agent": target.agent, "file": str(target.file), "diff": diff, "content": new_text}
        )
    return changes


def apply(changes: list[dict[str, Any]]) -> list[str]:
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    written = []
    for change in changes:
        if not change["diff"]:
            continue
        path = Path(change["file"])
        if path.exists():
            shutil.copy2(path, path.with_name(f"{path.name}.worklog-backup-{stamp}"))
        temporary = path.with_name(path.name + ".worklog-tmp")
        temporary.write_text(change["content"], encoding="utf-8")
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
        written.append(str(path))
    return written


def installed(agent: str) -> tuple[bool, str | None]:
    """Whether all WorkLog events are installed, and the command they run."""
    for target in targets():
        if target.agent != agent:
            continue
        _, data = read(target.file)
        found: dict[str, str] = {}
        for event, groups in data.get("hooks", {}).items():
            for group in groups if isinstance(groups, list) else []:
                for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                    command = str(hook.get("command", ""))
                    if MARKER.search(command):
                        found[event] = command
        complete = set(EVENTS) <= set(found)
        first = next(iter(found.values()), None)
        return complete, first.split(" hook ")[0] if first else None
    return False, None
