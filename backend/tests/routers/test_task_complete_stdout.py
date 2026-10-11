import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.session import get_db
from app.routers.task import router
from app.services import task_execution_service
from app.services.box_operations import ascii_box_service


@pytest.fixture
def boat_api():
    state = {
        "stdout": "", "truncated": False, "running": False,
        "read_error": False, "log_reads": 0, "exit_code": 0, "signal": None,
    }
    log_path = "/tmp/boat-process-1.stdout.log"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def error(self, status):
            self.respond(status, {
                "ok": False, "type": "error", "status": status,
                "code": "owned", "message": "owned fixture", "requestId": "owned",
                "error": {"code": "owned", "message": "owned fixture"},
            })

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path.endswith("/commands/1"):
                self.respond(200, {
                    "ok": True, "type": "command.status", "success": True,
                    "processId": 1, "status": "running" if state["running"] else "exited",
                    "running": state["running"],
                    "exitCode": None if state["running"] else state["exit_code"],
                    "signal": state["signal"],
                    "stdout": state["stdout"][-4096:] if state["truncated"] else state["stdout"],
                    "stderr": "", "stdoutTruncated": state["truncated"],
                    "logPath": log_path,
                })
            elif parsed.path.endswith("/files"):
                path = parse_qs(parsed.query)["path"][0]
                if path != log_path:
                    # The normal credential-renewal lookup has no fixture file.
                    self.error(404)
                    return
                state["log_reads"] += 1
                if state["read_error"]:
                    self.error(503)
                else:
                    self.respond(200, {
                        "ok": True, "type": "file.read", "success": True,
                        "path": path, "encoding": "utf8",
                        "size": len(state["stdout"].encode()), "content": state["stdout"],
                    })
            else:
                raise AssertionError(self.path)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@pytest.mark.parametrize(
    "truncated, is_error, running, read_error",
    [
        (True, False, False, False),
        (True, True, False, False),
        (False, False, False, False),
        (True, False, True, False),
        (True, False, False, True),
    ],
    ids=["complete-success", "complete-error", "short-control", "running-control", "read-error"],
)
async def test_completed_task_uses_full_log(
    db_session, user_id, monkeypatch, boat_api,
    truncated, is_error, running, read_error,
):
    report = "report \u2014 " * 1000 if truncated else "short report"
    base_url, state = boat_api
    state.update(
        stdout=json.dumps({"type": "result", "is_error": is_error, "result": report}),
        truncated=truncated, running=running, read_error=read_error,
    )
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    task = await task_execution_service.create_task(
        db_session, user_id=user_id, provider="claude", box_id="bx_abcdefgh",
        prompt_text="produce a report",
    )
    task.status = "running"
    task.prompt_id = "1"
    await db_session.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        response = await client.get(f"/api/v1/tasks/{task.id}")
    assert response.status_code == 200
    payload = response.json()
    if running:
        assert payload["status"] == "running"
        assert state["log_reads"] == 0
    elif read_error:
        assert payload["status"] == "failed"
        assert "503" in payload["error"]
        assert state["log_reads"] == 1
    else:
        assert payload["status"] == ("failed" if is_error else "succeeded")
        assert payload["error" if is_error else "result"] == report
        assert state["log_reads"] == int(truncated)


@pytest.mark.parametrize("exit_code,signal,expected", [(0, None, "succeeded"), (1, None, "failed"), (None, "SIGTERM", "failed")])
async def test_completed_task_respects_process_termination(monkeypatch, boat_api, exit_code, signal, expected):
    base_url, state = boat_api
    state.update(stdout=json.dumps({"is_error": False, "result": "partial output"}),
                 exit_code=exit_code, signal=signal)
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    task = SimpleNamespace(id=1, status="running", prompt_id="1", box_id="bx_abcdefgh")
    # Capture the actual SQL update boundary; this test does not use PostgreSQL.
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=0)), commit=AsyncMock())
    monkeypatch.setattr(task_execution_service, "_reload", AsyncMock(return_value=task))
    await task_execution_service.sync_task_status(db, task)
    values = db.execute.call_args.args[0].compile().params
    assert values["status"] == expected
    if expected == "failed":
        assert str(exit_code if signal is None else signal) in values["error"]
    else:
        assert values["result"] == "partial output"
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "stdout, expected_status, expected_text",
    [
        ("[]", "failed", "[]"),
        ("null", "failed", "null"),
        ('"unexpected"', "failed", '"unexpected"'),
        ("true", "failed", "true"),
        ("42", "failed", "42"),
        ("3.5", "failed", "3.5"),
        ('{"type":"result","is_error":false,"result":"report"}', "succeeded", "report"),
        ('{"type":"result","is_error":true,"result":"provider error"}', "failed", "provider error"),
        ("not JSON", "failed", "not JSON"),
    ],
    ids=["array", "null", "string", "boolean", "integer", "float",
         "success-control", "error-control", "invalid-json-control"],
)
async def test_completed_task_records_malformed_result_output(
    db_session, user_id, monkeypatch, boat_api, stdout, expected_status, expected_text,
):
    # The owned command emits malformed provider output, not a valid Claude result.
    # JSON syntax alone does not make the six non-object shapes result records.
    if stdout != "not JSON":
        decoded = json.loads(stdout)
        assert isinstance(decoded, dict) is (stdout.startswith("{"))
    assert len(stdout.encode()) < 128
    base_url, state = boat_api
    state.update(stdout=stdout, truncated=False, running=False)
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    task = await task_execution_service.create_task(
        db_session, user_id=user_id, provider="claude", box_id="bx_abcdefgh",
        prompt_text="produce a report",
    )
    task.status = "running"
    task.prompt_id = "1"
    await db_session.commit()

    # First admit the actual installed SDK over owned TCP without using the
    # production result parser as the expected status oracle.
    command = await run_in_threadpool(
        ascii_box_service.command_status, task.box_id, int(task.prompt_id),
    )
    assert not command.running
    assert command.stdout == stdout
    assert not command.stdout_truncated

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://owned",
    ) as client:
        response = await client.get(f"/api/v1/tasks/{task.id}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == expected_status
        assert payload["error" if expected_status == "failed" else "result"] == expected_text
        # A second poll must observe the same persisted terminal result.
        repeated = await client.get(f"/api/v1/tasks/{task.id}")
    assert repeated.status_code == 200
    assert repeated.json()["status"] == expected_status
    assert state["log_reads"] == 0
