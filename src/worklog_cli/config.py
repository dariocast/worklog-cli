"""XDG paths and validated, data-only TOML configuration."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worklog_cli.errors import WorklogError


def xdg_path(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def default_db() -> Path:
    return xdg_path("XDG_DATA_HOME", ".local/share") / "worklog/worklog.db"


def global_config() -> Path:
    return xdg_path("XDG_CONFIG_HOME", ".config") / "worklog/config.toml"


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError) as exc:
        raise WorklogError(f"Cannot read config {path}: {exc}", "config_error") from exc
    allowed = {"project", "tags"}
    if set(data) - allowed:
        raise WorklogError(f"Unknown config keys in {path}: {sorted(set(data) - allowed)}")
    if "project" in data and (not isinstance(data["project"], str) or not data["project"].strip()):
        raise WorklogError(f"project must be a non-empty string in {path}")
    if "tags" in data and (
        not isinstance(data["tags"], list)
        or any(not isinstance(tag, str) or not tag.strip() for tag in data["tags"])
    ):
        raise WorklogError(f"tags must be an array of non-empty strings in {path}")
    return data


@dataclass(frozen=True)
class Config:
    project: str | None
    tags: tuple[str, ...]


def resolve(project: str | None, tags: list[str] | None) -> Config:
    data = read_config(global_config())
    cwd = Path.cwd()
    for parent in (cwd, *cwd.parents):
        local = parent / ".worklog.toml"
        if local.is_file():
            data.update(read_config(local))
            break
    return Config(
        project if project is not None else data.get("project"),
        tuple(tags if tags is not None else data.get("tags", [])),
    )
