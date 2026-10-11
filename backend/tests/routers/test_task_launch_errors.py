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
    state = {"fail_path": None, "launches": 0, "commands": [], "files": {}}

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
            if urlsplit(self.path).path.endswith("/files"):
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
                state["files"][data["path"]] = data["content"]
                if data["path"] == state["fail_path"]:
                    self.failure(503)
                else:
                    self.respond(200, {
                        "ok": True, "type": "file.written", "success": True,
                        "path": data["path"], "encoding": "utf8",
                        "size": len(data["content"]),
                    })
            else:
                assert self.path.endswith("/commands")
                assert data["detached"] is True
                state["launches"] += 1
                state["commands"].append(data)
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
    "fail_path, credentials",
    [
        (None, True),
        (".claude/.credentials.json", True),
        (".claude/settings.json", True),
        (None, False),
    ],
    ids=[
        "success-control", "credential-write-failure", "hook-write-failure",
        "missing-control",
    ],
)
async def test_launch_setup_error_is_terminal(
    db_session, user_id, monkeypatch, boat_api, fail_path, credentials
):
    if credentials:
        await user_credential_service.save_token(
            db_session,
            user_id=user_id,
            provider="claude",
            file_path=".claude/.credentials.json",
            plaintext=json.dumps({
                "claudeAiOauth": {"expiresAt": 1, "accessToken": "owned-fixture"},
            }),
        )
    base_url, state = boat_api
    state["fail_path"] = fail_path
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://owned",
    ) as client:
        response = await client.post("/api/v1/tasks", json={
            "user_id": user_id, "provider": "claude", "box_id": "bx_abcdefgh",
            "prompt_text": "report",
        })
    task = (await db_session.execute(
        select(Tasks).where(Tasks.user_id == user_id)
    )).scalar_one()
    if fail_path is not None:
        assert (
            response.status_code,
            task.status,
            bool(task.error and "503" in task.error),
            state["launches"],
        ) == (502, "failed", True, 0)
        assert task.prompt_id is None
    elif credentials:
        assert response.status_code == 200
        assert task.status == "running"
        assert task.prompt_id == "1"
        assert state["launches"] == 1
    else:
        assert response.status_code == 400
        assert task.status == "failed"
        assert state["launches"] == 0
