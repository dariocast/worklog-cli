"""XDG paths and the global configuration: idle threshold and path mappings.

Mappings live only in the user's global config, never in repositories.
"""

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from worklog_cli.errors import WorklogError
from worklog_cli.timeutil import parse_duration

IGNORE = "ignore"
INBOX = "inbox"
RESERVED = {IGNORE, INBOX}
DEFAULT_IDLE = "15m"


def xdg_path(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def default_db() -> Path:
    return xdg_path("XDG_DATA_HOME", ".local/share") / "worklog/ledger.db"


def config_path() -> Path:
    return xdg_path("XDG_CONFIG_HOME", ".config") / "worklog/config.toml"


def state_dir() -> Path:
    return xdg_path("XDG_STATE_HOME", ".local/state") / "worklog"


def normalize_path(value: str | Path) -> Path:
    return Path(os.path.abspath(Path(value).expanduser()))


def display_path(path: Path) -> str:
    home = Path.home()
    return "~" + str(path)[len(str(home)) :] if path == home or home in path.parents else str(path)


@dataclass
class Config:
    idle_threshold: str = DEFAULT_IDLE
    paths: dict[str, str] = field(default_factory=dict)

    @property
    def idle_seconds(self) -> int:
        return 0 if self.idle_threshold in ("0", "0m") else parse_duration(self.idle_threshold)

    def resolve(self, directory: str | Path) -> str | None:
        """Project name, ``ignore``, or None for the inbox. Most specific path wins."""
        target = normalize_path(directory)
        best: tuple[int, str] | None = None
        for key, value in self.paths.items():
            base = normalize_path(key)
            if (target == base or base in target.parents) and (
                best is None or len(base.parts) > best[0]
            ):
                best = (len(base.parts), value)
        return best[1] if best else None


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except (OSError, ValueError) as exc:
        raise WorklogError(f"Cannot read config {path}: {exc}", "config_error") from exc
    unknown = set(data) - {"idle_threshold", "paths"}
    if unknown:
        raise WorklogError(f"Unknown config keys in {path}: {sorted(unknown)}", "config_error")
    idle = data.get("idle_threshold", DEFAULT_IDLE)
    paths = data.get("paths", {})
    if not isinstance(idle, str):
        raise WorklogError(f"idle_threshold must be a string like '15m' in {path}", "config_error")
    if not isinstance(paths, dict) or any(
        not isinstance(v, str) or not v.strip() for v in paths.values()
    ):
        raise WorklogError(f"[paths] values must be project names in {path}", "config_error")
    config = Config(idle, dict(paths))
    try:
        config.idle_seconds  # noqa: B018 - validate eagerly
    except WorklogError as exc:
        raise WorklogError(f"{exc} (idle_threshold in {path})", "config_error") from exc
    return config


def save(config: Config, path: Path | None = None) -> None:
    """Rewrite the config file. Comments are not preserved."""
    path = path or config_path()
    lines = [f"idle_threshold = {json.dumps(config.idle_threshold)}", "", "[paths]"]
    lines += [f"{json.dumps(k)} = {json.dumps(v)}" for k, v in sorted(config.paths.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)
