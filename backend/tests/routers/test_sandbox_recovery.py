import json
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telegram_bot
from app.core.config import settings
from app.db.models.user_sandbox import UserSandbox
from app.db.session import get_db
from app.routers.sandbox import router
from app.services import user_sandbox_service

OLD_BOX = "bx_abcdefgh"
NEW_BOX = "bx_23456789"


@pytest.fixture
def boat_api():
    """Owned Boat-protocol HTTP fixture; never connects to a Boat account."""
    state = {"existing_status": 200, "creates": 0, "create_status": 202}

    def sandbox(box_id):
        return {
            "id": box_id,
            "name": "owned",
            "state": "ready",
            "type": "small",
            "desktopAvailable": False,
            "snapshotAvailable": False,
        }

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

        def do_GET(self):
            box_id = self.path.rsplit("/", 1)[-1]
            assert box_id in {OLD_BOX, NEW_BOX}
            status = state["existing_status"] if box_id == OLD_BOX else 200
            if status != 200:
                self.respond(status, {
                    "ok": False, "type": "error", "status": status,
                    "code": "not_found" if status == 404 else "unavailable",
                    "message": "owned fixture", "requestId": "owned",
                    "error": {"code": "owned", "message": "owned fixture"},
                })
            else:
                self.respond(200, {
                    "ok": True, "type": "sandbox.info", "sandbox": sandbox(box_id),
                })

        def do_POST(self):
            assert self.path == "/sandboxes"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["ttlSeconds"] == 1800
            assert request["noEnv"] is True
            state["creates"] += 1
            if state["create_status"] != 202:
                self.respond(503, {
                    "ok": False, "type": "error", "status": 503,
                    "code": "unavailable", "message": "owned fixture",
                    "requestId": "owned",
                    "error": {"code": "unavailable", "message": "owned fixture"},
                })
                return
            self.respond(202, {
                "ok": True, "type": "sandbox.created", "status": "provisioning",
                "ttlSeconds": 1800, "sandbox": sandbox(NEW_BOX),
            })

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@pytest.mark.parametrize(
    "existing_status", [None, 200, 404, 503, 403],
    ids=["new-positive", "reuse-positive", "missing-recovery",
         "transient-control", "denied-control"],
)
async def test_ensure_handles_missing_sandbox(
    db_session: AsyncSession, user_id: str, monkeypatch, boat_api,
    existing_status: int | None
):
    if existing_status is not None:
        await user_sandbox_service.create(
            db_session, user_id=user_id, box_id=OLD_BOX,
            state="ready", machine_type="small", ttl_seconds=1800,
        )
    base_url, state = boat_api
    state["existing_status"] = existing_status
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        response = await client.post(
            "/api/v1/sandboxes/ensure",
            json={"user_id": user_id, "ttl_seconds": 1800},
        )
    if existing_status in {403, 503}:
        assert response.status_code == 502
        assert state["creates"] == 0
        current = await user_sandbox_service.get_active_by_user_id(db_session, user_id)
        assert current.box_id == OLD_BOX
    else:
        assert response.status_code == 200
        assert response.json()["box_id"] == (
            NEW_BOX if existing_status in {None, 404} else OLD_BOX
        )
        assert state["creates"] == (1 if existing_status in {None, 404} else 0)
        rows = (await db_session.execute(select(UserSandbox))).scalars().all()
        assert sum(row.is_active for row in rows) == 1
        assert response.json()["is_active"] is True


async def test_failed_replacement_rolls_back_then_retry_succeeds(
    db_session, user_id, monkeypatch, boat_api
):
    await user_sandbox_service.create(
        db_session, user_id=user_id, box_id=OLD_BOX,
        state="ready", machine_type="small", ttl_seconds=1800,
    )
    base_url, state = boat_api
    state.update(existing_status=404, create_status=503)
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        response = await client.post(
            "/api/v1/sandboxes/ensure", json={"user_id": user_id}
        )
        assert response.status_code == 502
        assert state["creates"] == 1
        current = await user_sandbox_service.get_active_by_user_id(db_session, user_id)
        assert current.box_id == OLD_BOX
        assert (await db_session.execute(select(UserSandbox))).scalars().all() == [
            current
        ]
        state["create_status"] = 202
        response = await client.post(
            "/api/v1/sandboxes/ensure", json={"user_id": user_id}
        )
        assert response.status_code == 200
        assert response.json()["box_id"] == NEW_BOX
        rows = (await db_session.execute(select(UserSandbox))).scalars().all()
        assert sum(row.is_active for row in rows) == 1
        assert state["creates"] == 2


@pytest.mark.parametrize(
    "existing_status", [None, 200, 404], ids=["bot-fresh", "bot-reuse", "bot-missing"]
)
async def test_bot_uses_same_sandbox_recovery(
    db_session, user_id, monkeypatch, boat_api, existing_status
):
    if existing_status is not None:
        await user_sandbox_service.create(
            db_session, user_id=user_id, box_id=OLD_BOX,
            state="ready", machine_type="small", ttl_seconds=1800,
        )
    base_url, state = boat_api
    state["existing_status"] = existing_status
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(telegram_bot, "AsyncSessionLocal", session)
    box_id = await telegram_bot._get_or_create_sandbox(user_id)
    assert box_id == (OLD_BOX if existing_status == 200 else NEW_BOX)
    assert state["creates"] == (0 if existing_status == 200 else 1)
