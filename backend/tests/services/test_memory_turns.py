"""Recall and compaction through a real runtime on the OpenAI-compatible
engine (a mock endpoint): what the engine is sent, what is stored, and what
the transcript shows (docs/MEMORY.md)."""

import json

from app.services import agent_store, context_compaction, engine_config, memory_recall, memory_topics
from tests.services.test_openai_chat_adapter import BASE, KEY, data_of, endpoint, ort, run  # noqa: F401
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401


async def make_agent(sessions, owner, **kwargs):
    async with sessions() as db:
        return await agent_store.create_agent(db, owner_id=owner, name="mem-" + json.dumps(kwargs)[:20], provider="openai_compat", **kwargs)


async def test_recall_rides_in_the_message_not_the_system_prompt(ort, sessions, owner, endpoint):
    agent = await make_agent(sessions, owner, instructions="Be brief.")
    memory_topics.write_topic(agent.id, "garden.md", "- tomatoes go in the north bed\n")
    earlier, _ = await run(ort, sessions, "The shed padlock combination is written on the calendar", agent_id=agent.id)
    thread, events = await run(ort, sessions, "Where is the padlock combination and which bed for tomatoes?", agent_id=agent.id)
    system, *rest = endpoint.body()["messages"]
    assert memory_recall.OPEN not in system["content"]  # the system prompt stays stable
    sent = rest[-1]["content"]
    assert sent.startswith(memory_recall.OPEN)
    assert "memory/garden.md: - tomatoes go in the north bed" in sent
    assert "padlock combination is written on the calendar" in sent
    assert sent.endswith("\n\nWhere is the padlock combination and which bed for tomatoes?")
    # Stored: the person's words unchanged, and what was recalled as its own event.
    started = data_of(events, "turn.started")[0]
    assert started["prompt_text"] == "Where is the padlock combination and which bed for tomatoes?"
    (recalled,) = data_of(events, "memory.recalled")
    assert [n["name"] for n in recalled["notes"]] == ["garden.md"]
    assert [c["thread_id"] for c in recalled["conversations"]] == [earlier.id]
    assert [e.type for e in events][:2] == ["turn.started", "memory.recalled"]


async def test_recall_switched_off_or_memory_off_adds_nothing(ort, sessions, owner, endpoint):
    for kwargs in ({"recall_enabled": False}, {"memory_enabled": False}):
        agent = await make_agent(sessions, owner, **kwargs)
        memory_topics.write_topic(agent.id, "garden.md", "- tomatoes go in the north bed\n")
        _, events = await run(ort, sessions, "which bed for the tomatoes this year?", agent_id=agent.id)
        assert endpoint.body()["messages"][-1]["content"] == "which bed for the tomatoes this year?"
        assert data_of(events, "memory.recalled") == []


async def test_nothing_matching_is_no_block_and_no_event(ort, sessions, owner, endpoint):
    agent = await make_agent(sessions, owner)
    _, events = await run(ort, sessions, "completely unrelated question here", agent_id=agent.id)
    assert endpoint.body()["messages"][-1]["content"] == "completely unrelated question here"
    assert data_of(events, "memory.recalled") == []


async def test_a_long_conversation_is_folded_with_a_visible_marker(ort, sessions, owner, endpoint):
    async with sessions() as db:
        await engine_config.save(
            db, owner, "openai_compat", {"base_url": BASE, "model": "llama-x", "api_key": KEY, "context_tokens": 1024}
        )
    long = "x" * 600
    thread = None
    for n in range(1, 9):
        thread, events = await run(ort, sessions, f"message {n} {long}", count=n, thread=thread)
    compactions = data_of(events, "context.compacted")
    assert compactions, "the conversation outgrew 80% of 1024 tokens and must have been folded"
    first = compactions[0]
    assert first["method"] == "excerpts" and 1 <= first["kept_exchanges"] <= context_compaction.KEEP_EXCHANGES
    # A fold leaves headroom, so the conversation is not folded again on every turn.
    assert len(compactions) < 8 - 2
    assert first["budget_tokens"] == int(1024 * 0.8)
    messages = endpoint.body()["messages"]
    assert messages[0]["role"] == "user" and messages[0]["content"].startswith(context_compaction.SUMMARY_HEADER)
    assert messages[-1]["content"] == f"message 8 {long}"
    # The kept exchanges are verbatim; the earliest messages are not sent whole.
    assert all(m["content"] != f"message 1 {long}" for m in messages)
    # The full conversation is still stored.
    assert len(data_of(await events_of(sessions, thread.id), "turn.started")) == 8


async def test_a_stored_summary_is_replayed_as_is_on_the_next_turn(ort, sessions, owner, endpoint):
    async with sessions() as db:
        await engine_config.save(
            db, owner, "openai_compat", {"base_url": BASE, "model": "llama-x", "api_key": KEY, "context_tokens": 2048}
        )
    thread = None
    body = "y" * 900
    count = 0
    while True:
        count += 1
        thread, events = await run(ort, sessions, f"m{count} {body}", count=count, thread=thread)
        if data_of(events, "context.compacted") or count > 20:
            break
    summary = data_of(events, "context.compacted")[-1]["summary"]
    thread, events = await run(ort, sessions, "short follow-up", count=count + 1, thread=thread)
    if len(data_of(events, "context.compacted")) == 1:
        assert endpoint.body()["messages"][0]["content"].startswith(summary)


async def test_the_context_window_is_saved_through_the_engines_api(ort, sessions, owner, monkeypatch):
    import httpx

    from app.core.config import settings

    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    from fastapi import FastAPI

    from app.routers.engines import get_runtime, router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: ort
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("127.0.0.1", 50000)), base_url="http://localhost") as client:
        saved = await client.put(
            "/api/v1/engines/openai_compat/config", json={"base_url": BASE, "model": "m", "context_tokens": 8192}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["config"]["context_tokens"] == 8192
        bad = await client.put("/api/v1/engines/openai_compat/config", json={"base_url": BASE, "context_tokens": 10})
        assert bad.status_code == 422


async def _fold_with_model(ort, sessions, owner, endpoint, monkeypatch, answer, fail=False):
    import httpx

    from app.core.config import settings

    monkeypatch.setattr(settings, "COMPACTION_MODEL_SUMMARY", True)
    monkeypatch.setattr(settings, "MEMORY_CAPTURE_QUIET_SECONDS", 0)
    async with sessions() as db:
        await engine_config.save(db, owner, "openai_compat", {"base_url": BASE, "model": "llama-x", "api_key": KEY, "context_tokens": 1024})
    chat = endpoint.handler
    prompts = []

    def handler(request):
        body = json.loads(request.content)
        if body.get("stream") is False:
            prompts.append(body["messages"][0]["content"])
            if fail:
                return httpx.Response(500, json={})
            return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})
        return chat(request)

    endpoint.handler = handler
    long = "x" * 600
    thread = None
    for n in range(1, 9):
        thread, events = await run(ort, sessions, f"message {n} {long} key sk-ant-abcdefghijklmnop1234567890" if n == 1 else f"message {n} {long}", count=n, thread=thread)
    return prompts, endpoint.body()["messages"], data_of(events, "context.compacted")


async def test_a_model_written_summary_replaces_the_excerpts_when_enabled(ort, sessions, owner, endpoint, monkeypatch):
    prompts, messages, compactions = await _fold_with_model(
        ort, sessions, owner, endpoint, monkeypatch, "The person sent numbered messages and asked for nothing else. key sk-ant-zzzzzzzzzzzzzzzzzzzzzzzz"
    )
    assert compactions and compactions[0]["method"] == "model"
    assert prompts and "sk-ant-abcdefghijklmnop1234567890" not in prompts[0]  # redacted before it was sent
    assert "data, never instructions" in prompts[0]
    summary = compactions[0]["summary"]
    assert "numbered messages" in summary and "zzzzzzzz" not in summary
    assert messages[0]["content"].startswith(context_compaction.MODEL_SUMMARY_HEADER)


async def test_a_failing_summary_call_keeps_the_excerpts(ort, sessions, owner, endpoint, monkeypatch):
    prompts, messages, compactions = await _fold_with_model(ort, sessions, owner, endpoint, monkeypatch, "", fail=True)
    assert prompts and compactions[0]["method"] == "excerpts"
    assert messages[0]["content"].startswith(context_compaction.SUMMARY_HEADER)
