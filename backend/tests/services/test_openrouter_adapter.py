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
    assert adapter.validate_config({"base_url": "  "}, saved)["api_key"] == KEY  # same address keeps the key
    assert adapter.public_config(saved) == {"base_url": OPENROUTER_BASE_URL, "model": MODEL, "has_api_key": True}
    for missing in ({}, {"api_key": "  "}, {"base_url": "https://proxy.test/v1"}):
        with pytest.raises(InvalidConfig):
            adapter.validate_config(missing, None)
    with pytest.raises(InvalidConfig):  # a new address drops the saved key, so one must be given again
        adapter.validate_config({"base_url": "https://proxy.test/v1"}, saved)
    with pytest.raises(InvalidConfig):
        adapter.validate_config({"clear_api_key": True}, saved)
    # Another OpenRouter-compatible address can still be set with its key.
    assert adapter.validate_config({"base_url": "https://proxy.test/v1/", "api_key": KEY}, None)["base_url"] == "https://proxy.test/v1"


async def test_without_a_key_it_is_not_ready_and_sends_nothing(endpoint):
    adapter = OpenRouterAdapter(transport=httpx.MockTransport(endpoint))
    for config in (None, {"base_url": OPENROUTER_BASE_URL}):
        adapter.config = config
        status, probe = await adapter.status(), await adapter.probe()
        assert not status.ready and "OpenRouter API key" in status.error
        assert not probe.ready and "OpenRouter API key" in probe.reason
    assert endpoint.requests == []


async def test_probe_lists_openrouter_models_with_the_headers(endpoint):
    endpoint.handler = lambda r: httpx.Response(200, json={"data": [{"id": "openai/gpt-4o-mini"}, {"id": MODEL}]})
    adapter = OpenRouterAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": OPENROUTER_BASE_URL, "api_key": KEY, "model": MODEL}
    probe = await adapter.probe()
    assert probe.ready and probe.signed_in is True
    assert [(m.id, m.default) for m in probe.models] == [("openai/gpt-4o-mini", False), (MODEL, True)]
    request = endpoint.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/models"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["x-title"] == "Open Duet" and request.headers["http-referer"].startswith("https://")


async def test_attribution_headers_go_only_to_openrouter_ai(endpoint):
    endpoint.handler = lambda r: httpx.Response(200, json={"data": []})
    adapter = OpenRouterAdapter(transport=httpx.MockTransport(endpoint))
    adapter.config = {"base_url": "https://proxy.test/v1", "api_key": KEY}
    await adapter.probe()
    proxied = endpoint.requests[-1]
    assert proxied.url.host == "proxy.test" and proxied.headers["authorization"] == f"Bearer {KEY}"
    assert "x-title" not in proxied.headers and "http-referer" not in proxied.headers
    for base in ("https://evil.test/openrouter.ai/v1", "https://openrouter.ai.evil.test/v1"):
        adapter.config = {"base_url": base, "api_key": KEY}
        await adapter.probe()
        assert "x-title" not in endpoint.requests[-1].headers
    adapter.config = {"base_url": OPENROUTER_BASE_URL, "api_key": KEY}
    await adapter.probe()
    direct = endpoint.requests[-1]
    assert direct.url.host == "openrouter.ai"
    assert direct.headers["x-title"] == "Open Duet"
    assert direct.headers["http-referer"] == "https://github.com/Anil-matcha/open-duet"


async def test_a_turn_goes_to_openrouter_with_its_own_saved_config(ort, sessions, owner, endpoint):
    async with sessions() as db:
        await engine_config.save(db, owner, "openrouter", OpenRouterAdapter().validate_config({"api_key": KEY}, None))
        await engine_config.save(db, owner, "openai_compat", {"base_url": "http://llm.test/v1", "model": "local"})
    thread = await ort.create_thread(provider="openrouter", model=MODEL)
    await ort.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "assistant.message") == [{"text": "Hello"}]
    request = endpoint.requests[0]
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
