"""Portable exports. Future formats can be added without touching the ledger."""

import csv
import io
import json
from typing import Any


def envelope(data: Any) -> dict[str, Any]:
    return {"schema_version": 1, "data": data}


def json_text(data: Any) -> str:
    return json.dumps(envelope(data), ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def csv_text(tasks: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    fields = [
        "task_id",
        "project",
        "title",
        "description",
        "status",
        "created_at",
        "completed_at",
        "notes",
        "tags",
        "external_references",
        "session_id",
        "started_at",
        "ended_at",
        "duration_seconds",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for task in tasks:
        base = {key: task[key] for key in fields if key in task and key != "duration_seconds"}
        base["task_id"] = task["id"]
        base["tags"] = json.dumps(task["tags"], ensure_ascii=False)
        base["external_references"] = json.dumps(task["external_references"], ensure_ascii=False)
        for session in task["sessions"] or [None]:
            row = dict(base)
            if session:
                row.update(
                    {
                        "session_id": session["id"],
                        "started_at": session["started_at"],
                        "ended_at": session["ended_at"],
                        "duration_seconds": session["duration_seconds"],
                    }
                )
            writer.writerow({key: safe_cell(value) for key, value in row.items()})
    return output.getvalue()
