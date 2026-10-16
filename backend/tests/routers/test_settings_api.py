import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.config import settings
from app.db.models import SettingAudit
from app.db.session import get_db
from app.desktop import launch_auth, profile
from app.routers.settings import router
from tests.services.test_app_settings import configured  # noqa: F401


@pytest.fixture
async def client(configured, monkeypatch):
    monkeypatch.setattr(launch_auth, "_token", b"t" * 43)
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://127.0.0.1")
    app = FastAPI()
    app.add_middleware(launch_auth.LaunchAuthMiddleware)
    app.include_router(router, prefix="/api/v1")
    async def database():
        async with configured() as db:
            yield db
    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("127.0.0.1", 1)), base_url="http://127.0.0.1") as api:
        yield api


TOKEN = {launch_auth.HEADER: "t" * 43}


async def test_read_and_write_require_the_launch_token(client):
    assert (await client.get("/api/v1/settings")).status_code == 401
    assert (await client.put("/api/v1/settings/LOCAL_TOOLS_ENABLED", json={"value": False, "expected_version": 0})).status_code == 401


async def test_api_versions_validation_and_audit(client, configured):
    before = await client.get("/api/v1/settings", headers=TOKEN)
    assert len(before.json()["settings"]) == 6
    assert "encryption_key" in before.json()["storage"]
    path = "/api/v1/settings/LOCAL_TOOLS_ENABLED"
    change = {"value": False, "expected_version": 0}
    result = await client.put(path, json=change, headers=TOKEN)
    assert result.status_code == 200
    assert result.json()["effective_value"] is False
    assert result.json()["applies"] == "next_run"
    assert (await client.put(path, json=change, headers=TOKEN)).status_code == 409
    assert (await client.put(path, json={"value": "false", "expected_version": 1}, headers=TOKEN)).status_code == 422
    assert (await client.put("/api/v1/settings/TOKEN_ENCRYPTION_KEYS", json=change, headers=TOKEN)).status_code == 404
    async with configured() as db:
        rows = (await db.execute(select(SettingAudit))).scalars().all()
        assert len(rows) == 1 and rows[0].new_value_json is False


async def test_no_settings_write_api_on_headless_installs(client, monkeypatch):
    monkeypatch.setattr(profile, "_current", None)
    assert (await client.get("/api/v1/settings")).status_code == 404
    assert (await client.put("/api/v1/settings/LOCAL_TOOLS_ENABLED", json={"value": True, "expected_version": 0})).status_code == 404


async def test_origin_and_proxy_headers_are_refused(client):
    for headers in ({"Origin": "https://untrusted.invalid"}, {"x-forwarded-for": "127.0.0.1"}):
        result = await client.get("/api/v1/settings", headers={**TOKEN, **headers})
        assert result.status_code == 403
