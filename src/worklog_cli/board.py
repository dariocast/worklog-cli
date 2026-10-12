"""Local web board: a page on 127.0.0.1 over the same ledger methods as the CLI.

The server keeps no state besides a per-run token. Each API request opens the
ledger, runs one ledger operation and closes it, so hooks are never blocked.
"""

import hmac
import json
import secrets
import sqlite3
import sys
import webbrowser
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from worklog_cli import config
from worklog_cli.errors import WorklogError
from worklog_cli.ledger import Ledger
from worklog_cli.timeutil import parse_iso

DONE_DAYS = 14
MAX_BODY = 64 * 1024
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/board.js": ("board.js", "text/javascript; charset=utf-8"),
    "/ui.css": ("ui.css", "text/css; charset=utf-8"),
    "/board.css": ("board.css", "text/css; charset=utf-8"),
}
STATUS = {"not_found": 404, "conflict": 409, "usage_error": 400, "validation_error": 400}
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def column(task: dict[str, Any]) -> str:
    if task["status"] == "done":
        return "done"
    if task["project"] == config.INBOX:
        return "inbox"
    return "progress" if task["started"] else "todo"


def board_data(ledger: Ledger, all_done: bool = False) -> dict[str, Any]:
    since = ledger.clock() - timedelta(days=DONE_DAYS)
    cwds: dict[str, list[str]] = {}
    for item in ledger.inbox():
        cwds.setdefault(item["task_id"], []).append(item["cwd"])
    tasks = []
    for task in sorted(ledger.tasks(), key=lambda t: (t["rank"], t["created_at"])):
        task["column"] = column(task)
        if (
            task["column"] == "done"
            and not all_done
            and (task["closed_at"] is None or parse_iso(task["closed_at"]) < since)
        ):
            continue
        task["directories"] = sorted(set(cwds.get(task["id"], [])))
        tasks.append(task)
    projects = [p["name"] for p in ledger.projects() if p["name"] not in config.RESERVED]
    return {"tasks": tasks, "projects": projects, "done_days": DONE_DAYS}


def text_field(body: dict[str, Any], name: str) -> str | None:
    value = body.get(name)
    if value is not None and not isinstance(value, str):
        raise WorklogError(f"{name} must be a string", "validation_error")
    return value


class Handler(BaseHTTPRequestHandler):
    server: "BoardServer"

    def log_message(self, format: str, *args: Any) -> None:
        """Requests are never logged: they may contain ledger content."""

    def do_GET(self) -> None:
        self.handle_request("GET")

    def do_POST(self) -> None:
        self.handle_request("POST")

    def do_PATCH(self) -> None:
        self.handle_request("PATCH")

    def do_DELETE(self) -> None:
        self.handle_request("DELETE")

    def do_OPTIONS(self) -> None:
        # Cross-origin preflights are never approved.
        self.send_json(403, error("forbidden", "Cross-origin requests are not allowed"))

    def handle_request(self, method: str) -> None:
        if self.headers.get("Host") not in self.server.hosts:
            self.send_json(403, error("forbidden", "Unexpected Host header"))
            return
        url = urlsplit(self.path)
        if not url.path.startswith("/api/"):
            self.send_static(method, url.path)
            return
        token = self.headers.get("X-Worklog-Token", "")
        if not hmac.compare_digest(token.encode(), self.server.token.encode()):
            self.send_json(403, error("forbidden", "Missing or wrong board token"))
            return
        try:
            body = self.read_body() if method in ("POST", "PATCH") else {}
            data = self.server.run(method, url.path, parse_qs(url.query), body)
            self.send_json(200, {"schema_version": 1, "data": data})
        except WorklogError as exc:
            self.send_json(STATUS.get(exc.code, 500), error(exc.code, str(exc)))
        except (sqlite3.Error, OSError) as exc:
            self.send_json(500, error("storage_error", str(exc)))

    def read_body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise WorklogError("Expected application/json", "usage_error")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise WorklogError("Request too large", "usage_error")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as exc:
            raise WorklogError("Invalid JSON body", "usage_error") from exc
        if not isinstance(body, dict):
            raise WorklogError("Expected a JSON object", "usage_error")
        return body

    def send_static(self, method: str, path: str) -> None:
        if method != "GET" or path not in STATIC:
            self.send_json(404, error("not_found", "Not found"))
            return
        name, content_type = STATIC[path]
        payload = resources.files("worklog_cli").joinpath("static", name).read_bytes()
        self.send(200, content_type, payload)

    def send_json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send(status, "application/json; charset=utf-8", payload)

    def send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)


def error(code: str, message: str) -> dict[str, Any]:
    return {"schema_version": 1, "error": {"code": code, "message": message}}


class BoardServer(HTTPServer):
    def __init__(self, db: Path, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.db = db
        self.token = secrets.token_urlsafe(24)
        bound = self.server_address[1]
        self.hosts = {f"127.0.0.1:{bound}", f"localhost:{bound}"}
        self.url = f"http://127.0.0.1:{bound}/#token={self.token}"

    def run(self, method: str, path: str, query: dict[str, list[str]], body: dict[str, Any]) -> Any:
        ledger = Ledger(self.db, config.load())
        try:
            ledger.drain()
            return dispatch(ledger, method, path, query, body)
        finally:
            ledger.close()


def dispatch(
    ledger: Ledger, method: str, path: str, query: dict[str, list[str]], body: dict[str, Any]
) -> Any:
    if path == "/api/board" and method == "GET":
        with ledger.transaction(write=False):
            return board_data(ledger, query.get("done") == ["all"])
    if path == "/api/tasks" and method == "POST":
        title = text_field(body, "title")
        if title is None:
            raise WorklogError("title is required", "validation_error")
        project = text_field(body, "project")
        return ledger.add_task(
            project or None, title, text_field(body, "ref"), text_field(body, "description")
        )
    prefix = "/api/tasks/"
    if path.startswith(prefix) and len(path) > len(prefix):
        task_id = path[len(prefix) :]
        if method == "PATCH":
            status = text_field(body, "status")
            if status not in (None, "open", "done"):
                raise WorklogError("status must be open or done", "validation_error")
            return ledger.edit(
                task_id,
                title=text_field(body, "title"),
                ref=text_field(body, "ref"),
                status=status,
                project=text_field(body, "project"),
                description=text_field(body, "description"),
                before=text_field(body, "before"),
                after=text_field(body, "after"),
                top=body.get("top") is True,
            )
        if method == "DELETE":
            # Recorded time is deleted only from the CLI, on purpose.
            return ledger.remove_task(task_id)
    raise WorklogError(f"No route for {method} {path}", "not_found")


def serve(db: Path, port: int = 0, *, open_browser: bool = True) -> int:
    server = BoardServer(db, port)
    print(f"WorkLog board on {server.url}\nPress Ctrl+C to stop.", flush=True)
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Board stopped.", file=sys.stderr)
    finally:
        server.server_close()
    return 0
