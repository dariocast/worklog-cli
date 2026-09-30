"""Billable entries: one row per local day and task, ready to copy elsewhere."""

import csv
import io
from datetime import date, tzinfo
from typing import Any

from worklog_cli.ledger import Ledger
from worklog_cli.timeline import per_day, spans
from worklog_cli.timeutil import human_duration, to_local


def round_to(seconds: float, step: int | None) -> float:
    if not step:
        return round(seconds, 6)
    return float(int(seconds / step + 0.5) * step)


def report(
    ledger: Ledger, start: date, end: date, tz: tzinfo | None, step: int | None
) -> dict[str, Any]:
    if end < start:
        raise ValueError("end before start")
    raws = ledger.raws()
    totals = per_day(spans(raws, ledger.idle), tz)
    notes: dict[tuple[date, str], list[str]] = {}
    for raw in sorted(raws, key=lambda r: r.start):
        key = (to_local(raw.start, tz).date(), raw.task_id)
        if raw.note and raw.note not in notes.setdefault(key, []):
            notes[key].append(raw.note)
    tasks = {t["id"]: t for t in ledger.tasks()}
    rows = []
    for (day, task_id), seconds in sorted(totals.items()):
        if not start <= day <= end or seconds <= 0:
            continue
        task = tasks[task_id]
        rows.append(
            {
                "day": day.isoformat(),
                "task_id": task_id,
                "key": task["ref"] or task["project"],
                "project": task["project"],
                "ref": task["ref"],
                "title": task["title"],
                "duration_seconds": round_to(seconds, step),
                "raw_seconds": round(seconds, 6),
                "notes": notes.get((day, task_id), []),
            }
        )
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "timezone": getattr(tz, "key", None) if tz is not None else "system",
        "rounding_seconds": step,
        "entries": rows,
        "duration_seconds": round(sum(r["duration_seconds"] for r in rows), 6),
    }


def table(data: dict[str, Any]) -> str:
    rows = data["entries"]
    if not rows:
        return f"No time recorded from {data['from']} to {data['to']}."
    key_width = max(len(r["key"]) for r in rows)
    title_width = min(40, max(len(r["title"]) for r in rows))
    lines = []
    for r in rows:
        title = r["title"] if len(r["title"]) <= 40 else r["title"][:39] + "…"
        line = (
            f"{r['day']}  {r['key']:<{key_width}}  {title:<{title_width}}  "
            f"{human_duration(r['duration_seconds']):>6}"
        )
        if r["notes"]:
            line += "  " + "; ".join(r["notes"])
        lines.append(line.rstrip())
    lines.append(f"Total {human_duration(data['duration_seconds'])}")
    return "\n".join(lines)


def safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def csv_text(data: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    fields = ["day", "key", "project", "ref", "task_id", "title", "hours", "minutes", "notes"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for r in data["entries"]:
        minutes = round(r["duration_seconds"] / 60)
        row = {
            **{k: r[k] for k in ("day", "key", "project", "ref", "task_id", "title")},
            "hours": round(r["duration_seconds"] / 3600, 2),
            "minutes": minutes,
            "notes": "; ".join(r["notes"]),
        }
        writer.writerow({k: safe_cell(v) for k, v in row.items()})
    return output.getvalue()
