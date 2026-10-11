import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.config import settings
from app.db.models.task import Tasks
from app.db.session import get_db
from app.routers.task import router
from app.services import user_credential_service


@pytest.fixture
def boat_api():
    state = {"launches": 0, "commands": []}

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

        def failure(self, status):
            self.respond(status, {
                "ok": False, "type": "error", "status": status,
                "code": "owned", "message": "owned fixture", "requestId": "owned",
                "error": {"code": "owned", "message": "owned fixture"},
            })

        def do_GET(self):
            if urlsplit(self.path).path.endswith("/commands/1"):
                self.respond(200, {
                    "ok": True, "type": "command.status", "success": True,
                    "processId": 1, "status": "exited", "running": False,
                    "exitCode": 0, "stdout": '{"is_error":false,"result":"first done"}',
                    "stderr": "", "stdoutTruncated": False,
                })
            elif urlsplit(self.path).path.endswith("/files"):
                self.failure(404)
            else:
                assert self.path == "/sandboxes/bx_abcdefgh"
                self.respond(200, {
                    "ok": True, "type": "sandbox.info", "sandbox": {
                        "id": "bx_abcdefgh", "name": "owned", "state": "ready",
                        "type": "small", "desktopAvailable": False,
                        "snapshotAvailable": False,
                    },
                })

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/files"):
                self.respond(200, {
                    "ok": True, "type": "file.written", "success": True,
                    "path": data["path"], "encoding": "utf8",
                    "size": len(data["content"]),
                })
            else:
                assert self.path.endswith("/commands")
                assert data["detached"] is True
                state["launches"] += 1
                state["commands"].append(data["command"])
                self.respond(200, {
                    "ok": True, "type": "command.started", "success": True,
                    "processId": 1, "pid": 1, "command": data["command"],
                    "startedAt": "2026-10-07T00:00:00Z",
                })

        do_PUT = do_POST

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
    "parent_launched", [False, True], ids=["never-launched", "launched-control"]
)
async def test_reply_uses_existing_remote_session_only(
    db_session, user_id, monkeypatch, boat_api, parent_launched
):
    async def save_credential():
        await user_credential_service.save_token(
            db_session, user_id=user_id, provider="claude",
            file_path=".claude/.credentials.json",
            plaintext=json.dumps({"claudeAiOauth": {"expiresAt": 1}}),
        )

    base_url, state = boat_api
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    request = {
        "user_id": user_id, "provider": "claude", "box_id": "bx_abcdefgh",
        "prompt_text": "first report",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        if parent_launched:
            await save_credential()
        parent_response = await client.post("/api/v1/tasks", json=request)
        parent = (await db_session.execute(
            select(Tasks).where(Tasks.user_id == user_id)
        )).scalar_one()
        if parent_launched:
            assert parent_response.status_code == 200
            completed = await client.get(f"/api/v1/tasks/{parent.id}")
            assert completed.json()["status"] == "succeeded"
            assert state["launches"] == 1
        else:
            assert parent_response.status_code == 400
            assert parent.status == "failed"
            assert parent.prompt_id is None
            assert state["launches"] == 0
            await save_credential()
        reply = await client.post("/api/v1/tasks", json={
            **request, "prompt_text": "continue report", "parent_task_id": parent.id,
        })
        assert reply.status_code == 200
    reply_payload = reply.json()
    assert reply_payload["session_id"] == parent.session_id
    expected_flag = "--resume" if parent_launched else "--session-id"
    assert state["commands"][-1].endswith(f"{expected_flag} {parent.session_id}")
