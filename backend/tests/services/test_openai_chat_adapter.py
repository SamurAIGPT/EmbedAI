"""The OpenAI-compatible chat engine against a fake endpoint (httpx
MockTransport), through a real runtime: streaming, error mapping, history,
and that the key never leaves the Authorization header."""

import asyncio
import json

import httpx
import pytest
from sqlalchemy import delete, select, text

from app.core.config import settings
from app.db.models import Agent, Event, Thread, Turn
from app.db.models.approval import Approval
from app.db.models.user_credential import UserCredential
from app.services import agent_store, engine_config, local_claude_runner
from app.services.openai_chat_adapter import (
    InvalidConfig,
    OpenAIChatAdapter,
    chat_messages,
    check_base_url,
)
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ProviderUnavailable, ThreadRuntime
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401

KEY = "sk-SECRET-1234567890abcdef"
BASE = "http://llm.test/v1"


def sse(*chunks, done=True) -> bytes:
    lines = [f"data: {json.dumps(c) if not isinstance(c, str) else c}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def delta(piece, finish=None):
    return {"choices": [{"delta": {"content": piece}, "finish_reason": finish}]}


class Endpoint:
    """What the fake endpoint saw, and how it answers the next request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.handler = lambda request: httpx.Response(200, content=sse(delta("Hel"), delta("lo"), delta("", "stop"), {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}), headers={"content-type": "text/event-stream"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def body(self, n=-1) -> dict:
        return json.loads(self.requests[n].content)


@pytest.fixture
def endpoint():
    return Endpoint()


@pytest.fixture
async def ort(sessions, owner, monkeypatch, endpoint, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    adapter = OpenAIChatAdapter(transport=httpx.MockTransport(endpoint))
    runtime = ThreadRuntime(hub=ThreadHub(), adapters={"openai_compat": adapter}, status_ttl=0, _session_factory=sessions, owner_id=owner)
    async with sessions() as db:
        await engine_config.save(db, owner, "openai_compat", {"base_url": BASE, "model": "llama-x", "api_key": KEY})
    yield runtime
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


def data_of(events, type_):
    return [e.data for e in events if e.type == type_]


async def run(ort, sessions, prompt="hi", count=1, thread=None, **kwargs):
    thread = thread or await ort.create_thread(provider="openai_compat", **kwargs)
    await ort.send_turn(thread.id, prompt)
    await finished(sessions, thread.id, count=count)
    return thread, await events_of(sessions, thread.id)


async def everything_stored(sessions, owner) -> str:
    """Every stored thing a key could leak into, as one string."""
    async with sessions() as db:
        parts = []
        for model in (Event, Turn, Thread, Approval):
            for row in (await db.execute(select(model))).scalars():
                parts.append(json.dumps({c.name: getattr(row, c.name) for c in model.__table__.columns}, default=str))
    return "\n".join(parts)


async def test_a_turn_streams_and_sends_the_key_only_as_a_header(ort, sessions, owner, endpoint, caplog, capsys):
    thread, events = await run(ort, sessions, "hi")
    assert data_of(events, "assistant.message") == [{"text": "Hello"}]
    done = data_of(events, "turn.completed")[0]
    assert done["result"] == "Hello"
    request = endpoint.requests[0]
    assert request.url == f"{BASE}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = endpoint.body()
    assert body["stream"] is True and body["model"] == "llama-x"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert KEY not in json.dumps(body)
    async with sessions() as db:
        turn = (await db.execute(select(Turn).where(Turn.thread_id == thread.id))).scalars().one()
    assert turn.status == "succeeded" and turn.usage == {"input_tokens": 7, "output_tokens": 2}
    out = capsys.readouterr()
    assert KEY not in caplog.text + out.out + out.err + await everything_stored(sessions, owner)


async def test_history_is_sent_with_the_agents_instructions(ort, sessions, owner, endpoint):
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="oa", provider="openai_compat", instructions="Be brief.", model="agent-model")
    thread, _ = await run(ort, sessions, "first", agent_id=agent.id)
    await run(ort, sessions, "second", count=2, thread=thread)
    body = endpoint.body()
    assert body["model"] == "agent-model"
    system, *rest = body["messages"]
    assert system["role"] == "system" and system["content"].startswith("Be brief.")
    assert [(m["role"], m["content"]) for m in rest] == [("user", "first"), ("assistant", "Hello"), ("user", "second")]


@pytest.mark.parametrize(
    "status, code",
    [(401, "endpoint_refused"), (403, "endpoint_refused"), (404, "endpoint_error"), (429, "endpoint_error"), (500, "endpoint_error")],
)
async def test_http_errors_map_to_codes_and_the_key_is_removed(ort, sessions, owner, endpoint, status, code, caplog):
    endpoint.handler = lambda r: httpx.Response(status, json={"error": {"message": f"bad key {KEY} for you"}})
    thread, events = await run(ort, sessions)
    failed = data_of(events, "turn.failed")[0]
    assert failed["error_code"] == code and str(status) in failed["error"]
    assert "[redacted]" in failed["error"]
    assert KEY not in failed["error"] and KEY not in caplog.text + await everything_stored(sessions, owner)


async def test_a_non_json_error_body_and_a_stream_error_chunk(ort, sessions, owner, endpoint):
    endpoint.handler = lambda r: httpx.Response(502, text=f"<html>gateway {KEY}</html>")
    _, events = await run(ort, sessions)
    failed = data_of(events, "turn.failed")[0]
    assert failed["error_code"] == "endpoint_error" and KEY not in failed["error"]
    endpoint.handler = lambda r: httpx.Response(200, content=sse(delta("par"), {"error": {"message": f"overloaded {KEY}"}}))
    thread, events = await run(ort, sessions, count=1)
    failed = data_of(events, "turn.failed")[-1]
    assert failed["error_code"] == "endpoint_error" and "overloaded" in failed["error"] and KEY not in failed["error"]


async def test_unreachable_and_timeout_map_without_leaking(ort, sessions, owner, endpoint):
    def refuse(request):
        raise httpx.ConnectError(f"cannot connect to {request.headers['authorization']}")

    endpoint.handler = refuse
    thread, events = await run(ort, sessions)
    failed = data_of(events, "turn.failed")[0]
    assert failed["error_code"] == "endpoint_unreachable" and KEY not in failed["error"]

    def slow(request):
        raise httpx.ReadTimeout("slow")

    endpoint.handler = slow
    _, events = await run(ort, sessions, "again", count=2, thread=thread)
    assert data_of(events, "turn.failed")[-1]["error_code"] == "timeout"
    assert KEY not in await everything_stored(sessions, owner)


async def test_length_cutoff_and_oversized_output_fail(ort, sessions, owner, endpoint, monkeypatch):
    endpoint.handler = lambda r: httpx.Response(200, content=sse(delta("cut", "length")))
    thread, events = await run(ort, sessions)
    assert data_of(events, "turn.failed")[0]["error_code"] == "length"
    monkeypatch.setattr(settings, "LOCAL_MAX_OUTPUT_BYTES", 10)
    endpoint.handler = lambda r: httpx.Response(200, content=sse(delta("x" * 50)))
    _, events = await run(ort, sessions, "more", count=2, thread=thread)
    assert data_of(events, "turn.failed")[-1]["error_code"] == "output_too_large"


async def test_no_model_and_no_config_fail_clearly(ort, sessions, owner, endpoint):
    async with sessions() as db:
        await engine_config.save(db, owner, "openai_compat", {"base_url": BASE})
    thread, events = await run(ort, sessions)
    assert data_of(events, "turn.failed")[0]["error_code"] == "no_model" and endpoint.requests == []
    async with sessions() as db:
        await engine_config.remove(db, owner, "openai_compat")
    with pytest.raises(ProviderUnavailable):
        await ort.send_turn(thread.id, "x")


async def test_a_run_that_no_longer_owns_its_turn_stops(ort, sessions, owner, endpoint):
    gate = asyncio.Event()

    async def hang(request):
        await gate.wait()
        return httpx.Response(200, content=sse())

    endpoint.handler = hang
    thread = await ort.create_thread(provider="openai_compat")
    await ort.send_turn(thread.id, "hi")
    await asyncio.sleep(0.3)
    await ort.stop_turn(thread.id)
    await finished(sessions, thread.id)
    gate.set()
    assert data_of(await events_of(sessions, thread.id), "turn.cancelled")


async def test_probe_lists_models_and_reports_a_refused_key():
    def handler(request):
        assert request.url.path == "/v1/models" and request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}, {"nope": 1}]})

    adapter = OpenAIChatAdapter(transport=httpx.MockTransport(handler))
    assert not (await adapter.probe()).ready
    adapter.config = {"base_url": BASE, "model": "z", "api_key": KEY}
    probe = await adapter.probe()
    assert probe.ready and probe.signed_in is True
    assert [(m.id, m.default) for m in probe.models] == [("z", True), ("a", False), ("b", False)]
    adapter = OpenAIChatAdapter(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    adapter.config = {"base_url": BASE, "api_key": KEY}
    refused = await adapter.probe()
    assert not refused.ready and refused.signed_in is False and KEY not in str(refused)

    def down(request):
        raise httpx.ConnectError(KEY)

    adapter = OpenAIChatAdapter(transport=httpx.MockTransport(down))
    adapter.config = {"base_url": BASE, "api_key": KEY}
    unreachable = await adapter.probe()
    assert not unreachable.ready and KEY not in str(unreachable)


def test_config_validation_keeps_the_key_only_for_the_same_address():
    adapter = OpenAIChatAdapter()
    saved = adapter.validate_config({"base_url": f"{BASE}/", "api_key": KEY, "model": "m"}, None)
    assert saved == {"base_url": BASE, "model": "m", "api_key": KEY}
    assert adapter.validate_config({"base_url": BASE}, saved)["api_key"] == KEY
    assert "api_key" not in adapter.validate_config({"base_url": "http://other.test/v1"}, saved)
    assert "api_key" not in adapter.validate_config({"base_url": BASE, "clear_api_key": True}, saved)
    assert adapter.public_config(saved) == {"base_url": BASE, "model": "m", "has_api_key": True}
    assert KEY not in json.dumps(adapter.public_config(saved))
    for bad in ("", "ftp://x", "http://u:p@x", "http://x?key=1", "http://x y", "nohost"):
        with pytest.raises(InvalidConfig):
            check_base_url(bad)
    with pytest.raises(InvalidConfig):
        adapter.validate_config({"base_url": BASE, "model": "-bad"}, None)
    with pytest.raises(InvalidConfig):
        adapter.validate_config({"base_url": BASE, "api_key": "has space"}, None)


def test_chat_messages_trim_history_and_start_with_the_user(monkeypatch):
    items = [
        {"role": "assistant", "text": "orphan"}, {"role": "user", "text": "a"},
        {"role": "assistant", "text": "b"}, {"role": "tool", "text": "x"}, {"role": "user", "text": "  "},
    ]
    assert chat_messages(items, None) == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    from app.services import openai_chat_adapter

    monkeypatch.setattr(openai_chat_adapter, "HISTORY_CHARS", 3)
    items = [{"role": "user", "text": "old old"}, {"role": "assistant", "text": "ok"}, {"role": "user", "text": "new"}]
    assert chat_messages(items, "sys") == [{"role": "system", "content": "sys"}, {"role": "user", "content": "new"}]
