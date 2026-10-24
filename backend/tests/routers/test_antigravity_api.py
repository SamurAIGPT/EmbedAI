"""Setup through the real router, database and isolated fixture ACP process."""
import asyncio
import json
import sys
import httpx
import pytest
from app.services import antigravity_runtime as runtime
from app.services.antigravity_adapter import AntigravityAdapter, profile_dir
from app.services.acp_adapter import _initialize_params
from app.services.stdio_rpc import StdioRpc
from tests.routers.test_engines_api import api, local_enabled, sessions, owner, cleanup  # noqa: F401
from tests.services.test_antigravity_adapter import SESSION, FAKE

@pytest.fixture
async def google(api, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "data_root", lambda: tmp_path / "google")
    monkeypatch.setattr(runtime, "installed", lambda: True)
    monkeypatch.setenv("OPEN_DOTS_ENGINE_ENV", "FAKE_*")
    (tmp_path / "plan.json").write_text(json.dumps({"load": True, "auth_write_token": True, "agent_info": {"name": "antigravity-acp", "version": runtime.VERSION}, "auth_methods": [{"id": "oauth-personal"}], "session": SESSION}))
    adapter = AntigravityAdapter()
    adapter.exit_grace_seconds = 0
    async def client(config, binary_dir=None):
        rpc = await StdioRpc.spawn([sys.executable, str(FAKE)], cwd=str(tmp_path), env=adapter.child_env(config), max_bytes=2_000_000, diagnostic=adapter._diagnostic)
        adapter.validate_initialize(await rpc.request("initialize", _initialize_params(), timeout=2))
        return rpc
    monkeypatch.setattr(adapter, "client", client)
    api.rt.adapters["antigravity"] = adapter
    return api, adapter

async def wait_done(api):
    for _ in range(200):
        status = (await api.get("/api/v1/engines/antigravity/setup/status")).json()
        if status["phase"] != "running": return status
        await asyncio.sleep(.01)
    pytest.fail("Setup did not complete")

async def test_sign_in_persistence_refresh_and_remove(google):
    api, adapter = google
    assert (await api.post("/api/v1/engines/antigravity/setup/sign_in")).status_code == 202
    status = await wait_done(api)
    assert status["phase"] == "succeeded", status
    response = await api.get("/api/v1/engines/antigravity?refresh=true")
    body = response.json()
    assert body["available"] and body["signed_in"] and not body["custom_models"]
    assert [m["id"] for m in body["models"]] == ["gemini-account", "gemini-other"]
    config = await api.rt.engine_config("antigravity")
    assert config["profile"] not in response.text and "refresh_token" not in response.text
    assert (await api.put("/api/v1/engines/antigravity/config", json={"model": "gemini-other"})).status_code == 200
    assert (await api.rt.engine_config("antigravity"))["profile"] == config["profile"]
    assert (await api.put("/api/v1/engines/antigravity/config", json={"api_key": "CANARY"})).status_code == 422
    assert (await api.post("/api/v1/engines/antigravity/setup/models")).status_code == 202
    assert (await wait_done(api))["phase"] == "succeeded"
    path = profile_dir(config)
    assert path.exists()
    assert (await api.delete("/api/v1/engines/antigravity/config")).status_code == 204
    assert not path.exists() and await api.rt.engine_config("antigravity") is None
    assert not (await api.get("/api/v1/engines/antigravity?refresh=true")).json()["available"]

async def test_failed_switch_keeps_account_and_successful_switch_cleans_old(google, monkeypatch):
    api, adapter = google
    await api.post("/api/v1/engines/antigravity/setup/sign_in")
    assert (await wait_done(api))["phase"] == "succeeded"
    first = await api.rt.engine_config("antigravity")
    original_client = adapter.client
    async def fail(*args, **kwargs): raise ValueError("GOOGLE_CODE_CANARY")
    monkeypatch.setattr(adapter, "client", fail)
    await api.post("/api/v1/engines/antigravity/setup/sign_in")
    status = await wait_done(api)
    assert status["phase"] == "failed" and "CANARY" not in str(status)
    assert await api.rt.engine_config("antigravity") == first and profile_dir(first).exists()
    monkeypatch.setattr(adapter, "client", original_client)
    await api.post("/api/v1/engines/antigravity/setup/sign_in")
    assert (await wait_done(api))["phase"] == "succeeded"
    assert (await api.rt.engine_config("antigravity"))["profile"] != first["profile"]
    assert not profile_dir(first).exists()

async def test_busy_mutations_and_cancel(google):
    api, adapter = google
    started = asyncio.Event()
    async def work():
        started.set()
        await asyncio.Event().wait()
    adapter.start_operation("sign_in", work)
    await started.wait()
    for method, path in [("post", "/setup/install"), ("delete", "/config"), ("put", "/config")]:
        response = await getattr(api, method)("/api/v1/engines/antigravity" + path, **({"json": {"model": "x"}} if method == "put" else {}))
        assert response.status_code == 409
    assert (await api.delete("/api/v1/engines/antigravity/setup")).status_code == 204
    assert (await wait_done(api))["phase"] == "cancelled"

async def test_invalid_callbacks_never_echo_codes(google):
    api, _ = google
    for payload in ({"operation_id": "expired", "callback_url": "http://127.0.0.1/?code=CODE_CANARY"}, {"operation_id": "x", "callback_url": ["CODE_CANARY"]}, None):
        response = await api.post("/api/v1/engines/antigravity/callback", json=payload)
        assert response.status_code == 422 and "CODE_CANARY" not in response.text

async def test_google_setup_is_local_only(google):
    api, _ = google
    transport = httpx.ASGITransport(api._transport.app, client=("203.0.113.9", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as remote:
        assert (await remote.post("/api/v1/engines/antigravity/setup/sign_in")).status_code == 403
        assert (await remote.get("/api/v1/engines/antigravity/setup/status")).status_code == 403
        assert (await remote.post("/api/v1/engines/antigravity/callback", json={})).status_code == 403
