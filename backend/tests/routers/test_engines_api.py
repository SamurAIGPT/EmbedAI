"""GET/PUT/DELETE /api/v1/engines, and engine validation on agents and threads.
Real routes, runtime and database; the OpenAI-compatible endpoint is a mock."""

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.routers.agents import get_runtime as agents_runtime
from app.routers.agents import router as agents_router
from app.routers.computers import get_runtime as computers_runtime
from app.routers.computers import router as computers_router
from app.routers.engines import get_runtime as engines_runtime
from app.routers.engines import router as engines_router
from app.routers.threads import get_runtime as threads_runtime
from app.routers.threads import router as threads_router
from app.services import local_claude_runner
from app.services.acp_adapter import AcpAdapter
from app.services.openai_chat_adapter import OpenAIChatAdapter
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.thread_harness import ReadyClaude, cleanup, owner, sessions  # noqa: F401

KEY = "sk-API-SECRET-abcdef123456"
FAKE_ACP = Path(__file__).parents[1] / "services" / "fake_acp_agent.py"


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture
async def api(sessions, owner, monkeypatch, tmp_path):
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    monkeypatch.setenv("FAKE_ACP_DIR", str(tmp_path))
    (tmp_path / "plan.json").write_text("{}")

    def models(request):
        return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})

    rt = ThreadRuntime(
        hub=ThreadHub(),
        adapters={
            "claude": ReadyClaude(),
            "acp": AcpAdapter(),
            "openai_compat": OpenAIChatAdapter(transport=httpx.MockTransport(models)),
        },
        status_ttl=0, _session_factory=sessions, owner_id=owner,
    )
    app = FastAPI()
    for router, dep in (
        (engines_router, engines_runtime), (agents_router, agents_runtime),
        (threads_router, threads_runtime), (computers_router, computers_runtime),
    ):
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[dep] = lambda: rt
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        client.rt = rt
        yield client
    await rt.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


def code_of(response) -> str:
    return response.json()["detail"]["error_code"]


async def test_the_list_and_one_engine(api):
    body = (await api.get("/api/v1/engines")).json()
    assert [e["id"] for e in body] == ["claude", "acp", "openai_compat"]
    by_id = {e["id"]: e for e in body}
    assert by_id["claude"]["available"] and by_id["claude"]["capabilities"]["computers"] is True
    acp = by_id["acp"]
    assert acp["kind"] == "acp" and acp["configurable"] and not acp["available"] and acp["config"] is None
    assert acp["custom_models"] is False and acp["capabilities"]["computers"] is False
    assert [field["name"] for field in acp["config_fields"]] == ["command", "label"]
    assert acp["setup"]["credential"] == "terminal" and acp["preset"] is False
    # Which engines can be the helper model: the ones with a quick text call.
    assert by_id["claude"]["can_write_text"] is True and acp["can_write_text"] is False
    assert by_id["openai_compat"]["can_write_text"] is True
    assert by_id["openai_compat"]["kind"] == "api" and by_id["openai_compat"]["capabilities"]["tools"] is False
    one = await api.get("/api/v1/engines/acp")
    assert one.status_code == 200 and one.json()["id"] == "acp"
    assert (await api.get("/api/v1/engines/nope")).status_code == 404
    assert (await api.get("/api/v1/engines/acp?refresh=true")).status_code == 200


async def test_engines_are_local_only(api, sessions):
    transport = httpx.ASGITransport(api._transport.app, client=("203.0.113.9", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as remote:
        assert (await remote.get("/api/v1/engines")).status_code == 403
        assert (await remote.put("/api/v1/engines/acp/config", json={"command": "x"})).status_code == 403


async def test_openai_config_is_encrypted_and_secrets_are_never_returned(api, sessions, owner):
    put = await api.put(
        "/api/v1/engines/openai_compat/config",
        json={"base_url": "http://llm.test/v1/", "model": "m1", "api_key": KEY},
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["config"] == {"base_url": "http://llm.test/v1", "model": "m1", "has_api_key": True}
    assert body["available"] and [m["id"] for m in body["models"]] == ["m1", "m2"] and body["default_model"] == "m1"
    assert KEY not in put.text
    for path in ("/api/v1/engines", "/api/v1/engines/openai_compat"):
        assert KEY not in (await api.get(path)).text
    async with sessions() as db:
        row = (await db.execute(select(UserCredential).where(UserCredential.user_id == owner, UserCredential.provider == "engine:openai_compat"))).scalar_one()
    stored = json.dumps({c.name: getattr(row, c.name) for c in UserCredential.__table__.columns}, default=str)
    assert KEY not in stored and "llm.test" not in stored  # encrypted at rest
    # Omitting the key keeps it for the same address; another address drops it.
    kept = (await api.put("/api/v1/engines/openai_compat/config", json={"base_url": "http://llm.test/v1"})).json()
    assert kept["config"]["has_api_key"] is True
    moved = (await api.put("/api/v1/engines/openai_compat/config", json={"base_url": "http://elsewhere.test/v1"})).json()
    assert moved["config"]["has_api_key"] is False
    cleared = await api.put("/api/v1/engines/openai_compat/config", json={"base_url": "http://llm.test/v1", "api_key": KEY})
    assert cleared.json()["config"]["has_api_key"] is True
    assert (await api.put("/api/v1/engines/openai_compat/config", json={"base_url": "http://llm.test/v1", "clear_api_key": True})).json()["config"]["has_api_key"] is False


async def test_delete_forgets_the_configuration(api, sessions, owner):
    await api.put("/api/v1/engines/openai_compat/config", json={"base_url": "http://llm.test/v1", "api_key": KEY})
    assert (await api.delete("/api/v1/engines/openai_compat/config")).status_code == 204
    entry = (await api.get("/api/v1/engines/openai_compat")).json()
    assert entry["config"] is None and not entry["available"]
    async with sessions() as db:
        assert (await db.execute(select(UserCredential).where(UserCredential.user_id == owner, UserCredential.provider == "engine:openai_compat"))).first() is None
    assert (await api.delete("/api/v1/engines/openai_compat/config")).status_code == 204  # idempotent


async def test_acp_config_and_the_validation_errors(api):
    put = await api.put("/api/v1/engines/acp/config", json={"command": f"{sys.executable} {FAKE_ACP}", "label": "Fake"})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["available"] and body["config"]["label"] == "Fake" and body["version"] == "fake-agent 1.2"
    for engine, payload in (
        ("acp", {"command": "  "}), ("acp", {}), ("openai_compat", {"base_url": "ftp://x"}),
        ("openai_compat", {"base_url": "http://x", "model": "-bad"}), ("openai_compat", {}),
    ):
        bad = await api.put(f"/api/v1/engines/{engine}/config", json=payload)
        assert bad.status_code == 422 and code_of(bad) == "invalid_engine_config", (engine, payload)
    assert (await api.put("/api/v1/engines/acp/config", json={"command": "x" * 1001})).status_code == 422
    assert code_of(await api.put("/api/v1/engines/claude/config", json={"command": "x"})) == "not_configurable"
    assert (await api.put("/api/v1/engines/nope/config", json={"command": "x"})).status_code == 404
    assert (await api.delete("/api/v1/engines/nope/config")).status_code == 404
    assert (await api.delete("/api/v1/engines/claude/config")).status_code == 409


async def test_fields_owned_by_another_engine_are_rejected(api):
    response = await api.put("/api/v1/engines/acp/config", json={"command": "x", "api_key": KEY})
    assert response.status_code == 422 and KEY not in response.text


async def test_agents_and_threads_validate_provider_and_model(api):
    bad = await api.post("/api/v1/agents", json={"name": "a1", "provider": "nope"})
    assert bad.status_code == 422 and code_of(bad) == "unknown_provider"
    bad = await api.post("/api/v1/agents", json={"name": "a2", "provider": "acp", "model": "gpt-9"})
    assert bad.status_code == 422 and code_of(bad) == "unknown_model"
    bad = await api.post("/api/v1/agents", json={"name": "a3", "provider": "openai_compat", "model": "-x"})
    assert bad.status_code == 422 and code_of(bad) == "unknown_model"
    bad = await api.post("/api/v1/threads", json={"provider": "nope"})
    assert bad.status_code == 422 and code_of(bad) == "unknown_provider"
    bad = await api.post("/api/v1/threads", json={"provider": "acp", "model": "gpt-9"})
    assert bad.status_code == 422 and code_of(bad) == "unknown_model"
    ok = await api.post("/api/v1/agents", json={"name": "a4", "provider": "openai_compat", "model": "anything:7b"})
    assert ok.status_code == 201
    thread = await api.post("/api/v1/threads", json={"provider": "openai_compat", "model": "anything:7b"})
    assert thread.status_code == 201 and thread.json()["provider"] == "openai_compat"
    # Patching an agent onto a bad engine or model is refused the same way.
    agent = ok.json()
    assert code_of(await api.patch(f"/api/v1/agents/{agent['id']}", json={"provider": "nope"})) == "unknown_provider"
    assert code_of(await api.patch(f"/api/v1/agents/{agent['id']}", json={"provider": "acp", "model": "x1"})) == "unknown_model"
    assert code_of(await api.patch(f"/api/v1/threads/{thread.json()['id']}", json={"model": "-bad"})) == "unknown_model"


async def test_computers_are_claude_only(api):
    other = (await api.post("/api/v1/agents", json={"name": "oa", "provider": "openai_compat"})).json()
    refused = await api.post(f"/api/v1/agents/{other['id']}/computer", json={})
    assert refused.status_code == 409 and code_of(refused) == "engine_without_computers"
    # An agent that has a computer cannot move to an engine without them.
    mine = (await api.post("/api/v1/agents", json={"name": "ca"})).json()
    from app.db.models import Computer

    rt = api.rt
    async with rt.session() as db:
        db.add(Computer(owner_id=rt.owner_id, agent_id=mine["id"], provider="docker", image="x", workspace_path="/tmp/x"))
        await db.commit()
    moved = await api.patch(f"/api/v1/agents/{mine['id']}", json={"provider": "acp"})
    assert moved.status_code == 409 and code_of(moved) == "engine_without_computers"
    async with rt.session() as db:
        await db.execute(delete(Computer).where(Computer.owner_id == rt.owner_id))
        await db.commit()
