"""A sandbox belongs to one user: its id alone never reaches another user's box."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db
from app.routers import sandbox as sandbox_router
from app.services import user_sandbox_service

OWNER_BOX = "bx_owner001"


class FakeSession:
    pass


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(sandbox_router.router)

    async def get_db_override():
        yield FakeSession()

    app.dependency_overrides[get_db] = get_db_override
    calls = []

    class Record:
        user_id = 1
        ttl_seconds = 60
        expires_at = None
        is_active = True

    async def get_by_box_id(db, box_id):
        return Record() if box_id == OWNER_BOX else None

    monkeypatch.setattr(user_sandbox_service, "get_by_box_id", get_by_box_id)
    monkeypatch.setattr(
        sandbox_router.ascii_box_service, "get_box", lambda box_id: calls.append(("get", box_id)) or (_ for _ in ()).throw(RuntimeError("reached the box"))
    )
    monkeypatch.setattr(
        sandbox_router.ascii_box_service, "run_command", lambda box_id, *a, **k: calls.append(("run", box_id)) or (_ for _ in ()).throw(RuntimeError("reached the box")),
        raising=False,
    )
    app.state.calls = calls
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/sandboxes/bx_unknown1"),
        ("POST", "/sandboxes/bx_unknown1/stop"),
        ("POST", "/sandboxes/bx_unknown1/test-command"),
        ("DELETE", "/sandboxes/bx_unknown1"),
    ],
)
async def test_a_box_the_app_never_created_is_not_reached(app, method, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path)
    assert response.status_code in (403, 404)
    assert app.state.calls == []
