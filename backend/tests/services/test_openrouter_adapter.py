"""The OpenRouter preset of the OpenAI-compatible engine, against a fake
endpoint (httpx MockTransport; nothing reaches openrouter.ai): its default
address, required key, attribution headers and its own saved configuration."""

import json

import httpx
import pytest
from sqlalchemy import delete

from app.core.config import settings
from app.db.models import Agent
from app.db.models.user_credential import UserCredential
from app.services import engine_config, local_claude_runner
from app.services.openai_chat_adapter import (
    OPENROUTER_BASE_URL,
    InvalidConfig,
    OpenAIChatAdapter,
    OpenRouterAdapter,
)
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ProviderUnavailable, ThreadRuntime
from tests.services.test_openai_chat_adapter import Endpoint, data_of
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401

KEY = "sk-or-v1-FAKE-0123456789abcdef"
MODEL = "meta-llama/llama-3.3-70b-instruct:free"


@pytest.fixture
def endpoint():
    return Endpoint()


@pytest.fixture
async def ort(sessions, owner, monkeypatch, endpoint, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    runtime = ThreadRuntime(
        hub=ThreadHub(),
        adapters={
            "openai_compat": OpenAIChatAdapter(transport=httpx.MockTransport(endpoint)),
            "openrouter": OpenRouterAdapter(transport=httpx.MockTransport(endpoint)),
        },
        status_ttl=0, _session_factory=sessions, owner_id=owner,
    )
    yield runtime
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


def test_config_defaults_the_address_and_requires_a_key():
    adapter = OpenRouterAdapter()
    saved = adapter.validate_config({"api_key": KEY, "model": MODEL}, None)
    assert saved == {"base_url": OPENROUTER_BASE_URL, "model": MODEL, "api_key": KEY}
    assert adapter.validate_config({}, saved)["api_key"] == KEY
    assert adapter.public_config(saved) == {"base_url": OPENROUTER_BASE_URL, "model": MODEL, "has_api_key": True}
    for payload in ({}, {"api_key": "  "}, {"base_url": "https://proxy.test/v1", "api_key": KEY}, {"clear_api_key": True}):
        with pytest.raises(InvalidConfig):
            adapter.validate_config(payload, None)


async def test_without_a_key_it_is_not_ready_and_sends_nothing(endpoint):
    adapter = OpenRouterAdapter(transport=httpx.MockTransport(endpoint))
    for config in (None, {"base_url": OPENROUTER_BASE_URL}):
        adapter.config = config
        status, probe = await adapter.status(), await adapter.probe()
        assert not status.ready and "OpenRouter API key" in status.error
        assert not probe.ready and "OpenRouter API key" in probe.reason
    assert endpoint.requests == []


async def test_probe_lists_openrouter_models_with_the_headers(endpoint):
    endpoint.handler = lambda r: httpx.Response(200, json={"data": {} if r.url.path.endswith("/key") else [{"id": "openai/gpt-4o-mini"}, {"id": MODEL}]})
    adapter = OpenRouterAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": OPENROUTER_BASE_URL, "api_key": KEY, "model": MODEL}
    probe = await adapter.probe()
    assert probe.ready and probe.signed_in is True
    assert [(m.id, m.default) for m in probe.models] == [(MODEL, True), ("openai/gpt-4o-mini", False)]
    assert endpoint.requests[0].url.path.endswith("/key")
    request = endpoint.requests[1]
    assert str(request.url) == "https://openrouter.ai/api/v1/models"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["x-title"] == "Open Duet" and request.headers["http-referer"].startswith("https://")


async def test_invalid_key_is_not_ready_even_with_public_models(endpoint):
    endpoint.handler = lambda r: httpx.Response(401, json={"error": {"message": KEY}}) if r.url.path.endswith("/key") else httpx.Response(200, json={"data": [{"id": MODEL}]})
    adapter = OpenRouterAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": OPENROUTER_BASE_URL, "api_key": KEY}
    probe = await adapter.probe()
    assert not probe.ready and probe.signed_in is False and KEY not in probe.reason
    assert len(endpoint.requests) == 1


async def test_old_proxy_config_never_receives_the_key(endpoint):
    adapter = OpenRouterAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": "https://proxy.test/v1", "api_key": KEY}
    assert not (await adapter.probe()).ready
    assert not (await adapter.status()).ready
    assert endpoint.requests == []


async def test_large_catalog_free_models_and_safe_default(endpoint):
    models = [{"id": f"paid/model-{i}"} for i in range(598)] + [
        {"id": "z/plain:free"}, {"id": "z/tools:free", "supported_parameters": ["tools"]},
    ]
    endpoint.handler = lambda r: httpx.Response(200, json={"data": {"is_free_tier": True} if r.url.path.endswith("/key") else models})
    adapter = OpenRouterAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": OPENROUTER_BASE_URL, "api_key": KEY}
    probe = await adapter.probe()
    assert len(probe.models) == 600
    assert probe.models[0].id == "z/tools:free" and probe.models[0].default
    assert "(free)" in probe.models[0].label
    endpoint.handler = lambda r: httpx.Response(200, json={"data": {"is_free_tier": False} if r.url.path.endswith("/key") else models})
    assert not any(model.default for model in (await adapter.probe()).models)


@pytest.mark.parametrize("status,body", [(500, {}), (200, {"data": []}), (200, "not json")])
async def test_failed_key_checks_do_not_report_ready(endpoint, status, body):
    endpoint.handler = lambda r: httpx.Response(status, json=body)
    adapter = OpenRouterAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": OPENROUTER_BASE_URL, "api_key": KEY}
    assert not (await adapter.probe()).ready


async def test_a_turn_goes_to_openrouter_with_its_own_saved_config(ort, sessions, owner, endpoint):
    async with sessions() as db:
        await engine_config.save(db, owner, "openrouter", OpenRouterAdapter().validate_config({"api_key": KEY}, None))
        await engine_config.save(db, owner, "openai_compat", {"base_url": "http://llm.test/v1", "model": "local"})
    previous = endpoint.handler
    endpoint.handler = lambda r: httpx.Response(200, json={"data": {}}) if r.url.path.endswith("/key") else previous(r) if previous else None
    thread = await ort.create_thread(provider="openrouter", model=MODEL)
    await ort.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "assistant.message") == [{"text": "Hello"}]
    request = next(r for r in endpoint.requests if r.method == "POST")
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}" and request.headers["x-title"] == "Open Duet"
    assert json.loads(request.content)["model"] == MODEL
    # The plain OpenAI-compatible engine keeps its own address and sends no OpenRouter headers.
    other = await ort.create_thread(provider="openai_compat")
    await ort.send_turn(other.id, "hi")
    await finished(sessions, other.id)
    local = endpoint.requests[-1]
    assert str(local.url) == "http://llm.test/v1/chat/completions"
    assert "x-title" not in local.headers and "authorization" not in local.headers


async def test_a_turn_without_a_saved_key_is_refused(ort, sessions, owner, endpoint):
    thread = await ort.create_thread(provider="openrouter", model=MODEL)
    with pytest.raises(ProviderUnavailable, match="OpenRouter API key"):
        await ort.send_turn(thread.id, "hi")
    assert endpoint.requests == []


async def test_openrouter_replays_a_continuing_conversation_on_fallback(ort, sessions, owner, endpoint, tmp_path):
    from app.services import agent_store
    from app.services.provider_adapter import Capabilities
    from tests.services.test_engine_fallback import Scripted, limited_before, replies

    primary = Scripted("primary", "Primary", replies, caps=Capabilities(resume=True), sessions=True)
    ort.adapters["primary"] = primary
    previous = endpoint.handler
    endpoint.handler = lambda r: httpx.Response(200, json={"data": {}}) if r.url.path.endswith("/key") else previous(r)
    async with sessions() as db:
        await engine_config.save(db, owner, "openrouter", OpenRouterAdapter().validate_config({"api_key": KEY}, None))
        agent = await agent_store.create_agent(
            db, owner_id=owner, name="Router fallback", provider="primary", workspace_path=str(tmp_path),
            fallback_provider="openrouter", fallback_model=MODEL,
        )
    thread = await ort.create_thread(agent_id=agent.id)
    await ort.send_turn(thread.id, "remember this")
    await finished(sessions, thread.id)
    primary.behaviour = limited_before
    await ort.send_turn(thread.id, "continue")
    await finished(sessions, thread.id, 2)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "engine.switched")
    posted = next(r for r in endpoint.requests if r.method == "POST")
    messages = json.loads(posted.content)["messages"]
    assert [m["content"] for m in messages if m["role"] == "user"] == ["remember this", "continue"]
    assert KEY not in json.dumps(events, default=str)
