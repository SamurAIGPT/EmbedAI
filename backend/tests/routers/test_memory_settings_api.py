"""The installation-wide memory settings over HTTP (docs/MEMORY.md)."""

import httpx
import pytest
from fastapi import FastAPI

from app.auth import policy
from app.core.config import settings
from app.routers.memory import router as memory_router
from app.services import memory_journal


@pytest.fixture
async def api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
    monkeypatch.setattr(memory_journal, "state_root", lambda: tmp_path / "state")
    app = FastAPI()
    app.include_router(memory_router, prefix="/api/v1")
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield client


async def test_settings_are_read_and_changed(api):
    first = await api.get("/api/v1/memory/settings")
    assert first.status_code == 200 and first.json() == {"learn_from_rooms": False, "tidy_hour": 3, "helper_models": {}}
    body = {"learn_from_rooms": True, "tidy_hour": 5, "helper_models": {"claude": "sonnet"}}
    saved = await api.put("/api/v1/memory/settings", json=body)
    assert saved.status_code == 200 and saved.json() == body
    assert (await api.get("/api/v1/memory/settings")).json() == body
    assert (await api.put("/api/v1/memory/settings", json={"learn_from_rooms": False})).json()["tidy_hour"] == 5
    bad = await api.put("/api/v1/memory/settings", json={"tidy_hour": 30})
    assert bad.status_code == 422 and bad.json()["detail"]["error_code"] == "invalid_setting"
    assert (await api.put("/api/v1/memory/settings", json={"nope": 1})).status_code == 422
    assert policy.required_scope("PUT", "/api/v1/memory/settings") == "admin"
