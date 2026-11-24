"""What WorkLog knows about each agent's chats: how to resume one, and the user-given
titles of Codex chats.

Codex is the one agent whose file WorkLog reads: ``session_index.jsonl`` holds only
chat IDs and the names users gave them, never prompts. Any problem with it falls back
to no title, silently.
"""

import json
import os
import re
from pathlib import Path

from worklog_cli.context import quote

RESUME = {
    "claude": "claude --resume {id}",
    "codex": "codex resume {id}",
    "agy": "agy --conversation {id}",
}
PLAIN = re.compile(r"[A-Za-z0-9._-]+")


def resume(agent: str, session_id: str, cwd: str) -> str | None:
    template = RESUME.get(agent)
    if template is None:
        return None
    command = template.format(id=session_id if PLAIN.fullmatch(session_id) else quote(session_id))
    return f"cd {quote(cwd)} && {command}" if cwd else command


def codex_titles() -> dict[str, str]:
    """Codex chat ID → the name the user gave it; the last line for an ID wins."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    titles: dict[str, str] = {}
    try:
        with (home / "session_index.jsonl").open(encoding="utf-8", errors="strict") as lines:
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                chat, name = entry.get("id"), entry.get("thread_name")
                if isinstance(chat, str) and isinstance(name, str):
                    if name.strip():
                        titles[chat] = name.strip()
                    else:
                        titles.pop(chat, None)
    except (OSError, UnicodeDecodeError):
        return {}
    return titles
