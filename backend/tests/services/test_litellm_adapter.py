"""The LiteLLM engine against a fake `litellm` module (no SDK or network
needed), through a real runtime: streaming, credentials left to the
environment unless saved, error mapping, and that a saved key never leaks."""

import json
import sys
import types
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models import Agent, Turn
from app.db.models.user_credential import UserCredential
from app.services import agent_store, engine_config, litellm_adapter, local_claude_runner
from app.services.engine_registry import default_adapters
from app.services.litellm_adapter import NOT_INSTALLED, LiteLLMAdapter
from app.services.openai_chat_adapter import InvalidConfig
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.test_openai_chat_adapter import data_of, everything_stored
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401

KEY = "sk-ant-FAKE-0123456789abcdef"
MODEL = "anthropic/claude-sonnet-4-6"


def chunk(piece=None, finish=None, usage=None):
    choices = [] if piece is None and finish is None else [
        SimpleNamespace(delta=SimpleNamespace(content=piece), finish_reason=finish)
    ]
    return SimpleNamespace(choices=choices, usage=usage)


class AuthenticationError(Exception):
    status_code = 401

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class RateLimitError(Exception):
    status_code = 429


class APIConnectionError(Exception):
    pass


class Timeout(APIConnectionError):
    pass


class FakeLiteLLM(types.ModuleType):
    """Records every `acompletion` call and streams canned chunks back."""

    def __init__(self):
        super().__init__("litellm")
        self.__version__ = "1.105.3"
        self.calls: list[dict] = []
        self.chunks = [chunk("Hel"), chunk("lo"), chunk("", "stop"), chunk(usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2))]
        self.error: Exception | None = None
        self.missing_keys: list[str] = []
        self.models_by_provider = {"anthropic": ["claude-haiku-4-5", "claude-sonnet-4-6"]}

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        chunks = list(self.chunks)

        async def stream():
            for item in chunks:
                yield item

        return stream()

    def validate_environment(self, model):
        return {"keys_in_environment": not self.missing_keys, "missing_keys": self.missing_keys}


@pytest.fixture
def fake(monkeypatch):
    module = FakeLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", module)
    monkeypatch.setattr(litellm_adapter, "litellm_installed", lambda: True)
    return module


@pytest.fixture
async def ort(sessions, owner, monkeypatch, fake, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"litellm": LiteLLMAdapter()}, status_ttl=0,
        _session_factory=sessions, owner_id=owner,
    )
    async with sessions() as db:
        await engine_config.save(db, owner, "litellm", LiteLLMAdapter().validate_config({"model": MODEL}, None))
    yield runtime
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


async def run(ort, sessions, prompt="hi", count=1, thread=None, **kwargs):
    thread = thread or await ort.create_thread(provider="litellm", **kwargs)
    await ort.send_turn(thread.id, prompt)
    await finished(sessions, thread.id, count=count)
    return thread, await events_of(sessions, thread.id)


def test_registered_as_a_chat_engine_like_the_openai_compatible_one():
    adapters = default_adapters()
    assert isinstance(adapters["litellm"], LiteLLMAdapter)
    assert adapters["litellm"].capabilities == adapters["openai_compat"].capabilities
    assert adapters["litellm"].kind == "api" and adapters["litellm"].replays_history


async def test_a_turn_streams_with_the_providers_own_credentials(ort, sessions, owner, fake):
    thread, events = await run(ort, sessions, "hi")
    assert data_of(events, "assistant.message") == [{"text": "Hello"}]
    call = fake.calls[0]
    assert call["model"] == MODEL and call["stream"] is True and call["drop_params"] is True
    assert call["stream_options"] == {"include_usage": True}
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    # Nothing saved: the SDK reads ANTHROPIC_API_KEY and friends itself.
    assert "api_key" not in call and "api_base" not in call
    async with sessions() as db:
        turn = (await db.execute(select(Turn).where(Turn.thread_id == thread.id))).scalars().one()
    assert turn.status == "succeeded" and turn.usage == {"input_tokens": 7, "output_tokens": 2}


async def test_a_saved_key_and_api_base_reach_only_the_sdk_call(ort, sessions, owner, fake, caplog, capsys):
    async with sessions() as db:
        await engine_config.save(db, owner, "litellm", LiteLLMAdapter().validate_config(
            {"model": "azure/gpt-4o-deployment", "base_url": "https://acme.openai.azure.com", "api_key": KEY}, None
        ))
    await run(ort, sessions)
    call = fake.calls[0]
    assert call["api_key"] == KEY and call["api_base"] == "https://acme.openai.azure.com"
    out = capsys.readouterr()
    assert KEY not in caplog.text + out.out + out.err + await everything_stored(sessions, owner)


async def test_the_agents_model_and_instructions_and_history(ort, sessions, owner, fake):
    async with sessions() as db:
        agent = await agent_store.create_agent(
            db, owner_id=owner, name="ll", provider="litellm", instructions="Be brief.", model="gemini/gemini-2.5-pro"
        )
    thread, _ = await run(ort, sessions, "first", agent_id=agent.id)
    await run(ort, sessions, "second", count=2, thread=thread)
    call = fake.calls[-1]
    assert call["model"] == "gemini/gemini-2.5-pro"
    system, *rest = call["messages"]
    assert system["role"] == "system" and system["content"].startswith("Be brief.")
    assert [(m["role"], m["content"]) for m in rest] == [("user", "first"), ("assistant", "Hello"), ("user", "second")]


@pytest.mark.parametrize(
    "error, code",
    [
        (AuthenticationError(f"invalid x-api-key {KEY}"), "endpoint_refused"),
        (RateLimitError("rate limit exceeded"), "engine_limited"),
        (APIConnectionError("connection refused"), "endpoint_unreachable"),
        (Timeout("read timed out"), "timeout"),
        (ValueError("model not found"), "endpoint_error"),
    ],
)
async def test_provider_errors_map_to_codes_without_the_key(ort, sessions, owner, fake, error, code):
    async with sessions() as db:
        await engine_config.save(db, owner, "litellm", LiteLLMAdapter().validate_config({"model": MODEL, "api_key": KEY}, None))
    fake.error = error
    _, events = await run(ort, sessions)
    failed = data_of(events, "turn.failed")[0]
    assert failed["error_code"] == code
    assert KEY not in json.dumps(failed) and KEY not in await everything_stored(sessions, owner)


async def test_length_cutoff_and_oversized_output_fail(ort, sessions, owner, fake, monkeypatch):
    fake.chunks = [chunk("cut", "length")]
    thread, events = await run(ort, sessions)
    assert data_of(events, "turn.failed")[0]["error_code"] == "length"
    monkeypatch.setattr(settings, "LOCAL_MAX_OUTPUT_BYTES", 10)
    fake.chunks = [chunk("x" * 50)]
    _, events = await run(ort, sessions, "more", count=2, thread=thread)
    assert data_of(events, "turn.failed")[-1]["error_code"] == "output_too_large"


async def test_probe_reports_install_config_and_missing_environment(fake, monkeypatch):
    adapter = LiteLLMAdapter()
    assert not (await adapter.probe()).ready
    adapter.config = {"model": MODEL}
    probe = await adapter.probe()
    assert probe.ready and probe.version == "1.105.3"
    assert [(m.id, m.default) for m in probe.models] == [(MODEL, True), ("anthropic/claude-haiku-4-5", False)]
    fake.missing_keys = ["ANTHROPIC_API_KEY"]
    missing = await adapter.probe()
    assert not missing.ready and "ANTHROPIC_API_KEY" in missing.reason
    adapter.config = {"model": MODEL, "api_key": KEY}
    saved = await adapter.probe()
    assert saved.ready and saved.signed_in is True and KEY not in str(saved)
    monkeypatch.setattr(litellm_adapter, "litellm_installed", lambda: False)
    absent = await adapter.probe()
    assert not absent.installed and absent.reason == NOT_INSTALLED
    assert not (await adapter.status()).ready


def test_config_validation_keeps_the_key_only_for_the_same_provider_and_address():
    adapter = LiteLLMAdapter()
    saved = adapter.validate_config({"model": f" {MODEL} ", "api_key": KEY}, None)
    assert saved == {"model": MODEL, "api_key": KEY}
    assert adapter.validate_config({"model": "anthropic/claude-haiku-4-5"}, saved)["api_key"] == KEY
    assert "api_key" not in adapter.validate_config({"model": "gemini/gemini-2.5-pro"}, saved)
    assert "api_key" not in adapter.validate_config({"model": MODEL, "base_url": "http://proxy.test/v1"}, saved)
    assert "api_key" not in adapter.validate_config({"model": MODEL, "clear_api_key": True}, saved)
    assert adapter.public_config(saved) == {"base_url": None, "model": MODEL, "has_api_key": True}
    for bad in ({}, {"model": "claude-sonnet-4-6"}, {"model": "-bad/x"}, {"model": MODEL, "api_key": "has space"},
                {"model": MODEL, "base_url": "ftp://x"}):
        with pytest.raises(InvalidConfig):
            adapter.validate_config(bad, None)
