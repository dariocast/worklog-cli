import http.client
import json
import threading
from datetime import timedelta

import pytest
from conftest import at

from worklog_cli.board import BoardServer, board_data
from worklog_cli.ledger import Ledger


@pytest.fixture
def server(tmp_path):
    board = BoardServer(tmp_path / "ledger.db")
    thread = threading.Thread(target=board.serve_forever, daemon=True)
    thread.start()
    yield board
    board.shutdown()
    board.server_close()


def call(server, method, path, body=None, *, token=True, host=None, content_type=None):
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if token:
        headers["X-Worklog-Token"] = server.token
    payload = None
    if body is not None:
        payload = json.dumps(body)
        headers["Content-Type"] = content_type or "application/json"
    connection.request(method, path, payload, headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    kind = response.getheader("Content-Type", "")
    return response.status, json.loads(raw) if kind.startswith("application/json") else raw


def test_page_is_served_with_strict_headers(server):
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/", headers={"Host": f"localhost:{port}"})
    response = connection.getresponse()
    page = response.read().decode()
    assert response.status == 200 and "/board.js" in page
    assert "default-src 'self'" in response.getheader("Content-Security-Policy")
    assert "http" not in page.replace("http-equiv", "")  # no external resources
    for asset in ("/board.js", "/ui.css", "/board.css"):
        status, _ = call(server, "GET", asset, token=False)
        assert status == 200
    assert server.url.startswith(f"http://127.0.0.1:{port}/#token=")


@pytest.mark.parametrize(
    "kwargs,method",
    [
        ({"token": False}, "GET"),
        ({"host": "evil.example:80"}, "GET"),
        ({}, "OPTIONS"),
    ],
)
def test_requests_without_token_right_host_or_same_origin_are_refused(server, kwargs, method):
    status, body = call(server, method, "/api/board", **kwargs)
    assert status == 403 and body["error"]["code"] == "forbidden"


def test_mutations_require_json_content_type(server):
    status, body = call(server, "POST", "/api/tasks", {"title": "x"}, content_type="text/plain")
    assert status == 400 and body["error"]["code"] == "usage_error"


def test_capture_triage_order_done_and_delete(server):
    status, capture = call(server, "POST", "/api/tasks", {"title": "Read the proposal"})
    assert status == 200 and capture["data"]["project"] == "inbox"
    capture_id = capture["data"]["id"]
    status, body = call(server, "POST", "/api/tasks", {"title": "x", "project": "typo"})
    assert status == 404
    ledger = Ledger(server.db)
    ledger.add_project("example-project")
    ledger.close()
    _, other = call(server, "POST", "/api/tasks", {"title": "Other", "project": "example-project"})
    other_id = other["data"]["id"]

    status, moved = call(
        server,
        "PATCH",
        f"/api/tasks/{capture_id}",
        {"project": "example-project", "after": other_id},
    )
    assert status == 200 and moved["data"]["project"] == "example-project"
    _, board = call(server, "GET", "/api/board")
    columns = [(t["title"], t["column"]) for t in board["data"]["tasks"]]
    assert columns == [("Other", "todo"), ("Read the proposal", "todo")]
    assert board["data"]["projects"] == ["example-project"]

    status, back = call(server, "PATCH", f"/api/tasks/{capture_id}", {"project": "inbox"})
    assert status == 400 and "reserved" in back["error"]["message"]
    _, done = call(server, "PATCH", f"/api/tasks/{capture_id}", {"status": "done"})
    assert done["data"]["status"] == "done" and done["data"]["closed_at"]
    status, _ = call(server, "PATCH", f"/api/tasks/{capture_id}", {"status": "maybe"})
    assert status == 400

    status, removed = call(server, "DELETE", f"/api/tasks/{capture_id}")
    assert status == 200 and removed["data"]["removed_intervals"] == 0
    status, _ = call(server, "DELETE", f"/api/tasks/{capture_id}")
    assert status == 404


def test_delete_refuses_recorded_time(server):
    ledger = Ledger(server.db)
    ledger.add_project("example-project")
    logged = ledger.log(at("09:00"), at("10:00"), project="example-project", title="Meeting")
    ledger.close()
    status, body = call(server, "DELETE", f"/api/tasks/{logged['task_id']}")
    assert status == 409 and body["error"]["code"] == "conflict"


def test_board_columns_and_done_window(ledger, clock, tmp_path):
    ledger.add_project("example-project")
    capture = ledger.add_task(None, "Capture")
    planned = ledger.add_task("example-project", "Planned")
    logged = ledger.log(at("09:00"), at("10:00"), project="example-project", title="Meeting")
    old = ledger.add_task("example-project", "Old")
    ledger.edit(old["id"], status="done")
    clock[0] += timedelta(days=15)
    recent = ledger.add_task("example-project", "Recent")
    ledger.edit(recent["id"], status="done")
    columns = {t["id"]: t["column"] for t in board_data(ledger)["tasks"]}
    assert columns == {
        capture["id"]: "inbox",
        planned["id"]: "todo",
        logged["task_id"]: "progress",
        recent["id"]: "done",
    }
    assert old["id"] in {t["id"] for t in board_data(ledger, all_done=True)["tasks"]}


def test_inbox_cards_show_their_directories(ledger, tmp_path):
    ledger.apply_event(
        {
            "event": "prompt",
            "agent": "claude",
            "session_id": "s1",
            "cwd": str(tmp_path / "elsewhere"),
            "ts": at("09:00").isoformat(),
        }
    )
    (task,) = board_data(ledger)["tasks"]
    assert task["column"] == "inbox" and task["directories"] == [str(tmp_path / "elsewhere")]
