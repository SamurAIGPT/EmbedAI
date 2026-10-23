"""The model-written compaction summary (docs/MEMORY.md, "Context compaction"):
on by default, a per-agent opt-out, a 20 second limit, Stop ending the whole
operation, a late answer never applied, any failure keeping the excerpts."""

import asyncio
import json

import httpx
import pytest

from app.core.config import settings
from app.services import agent_store, context_compaction, engine_config
from app.services.provider_adapter import ReplayAborted
from tests.services.test_memory_turns import _fold_with_model
from tests.services.test_openai_chat_adapter import BASE, KEY, data_of, endpoint, ort, run  # noqa: F401
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401


def test_the_model_written_summary_is_on_by_default():
    from app.core.config import Settings

    assert Settings.model_fields["COMPACTION_MODEL_SUMMARY"].default is True


async def _save_window(sessions, owner):
    async with sessions() as db:
        await engine_config.save(
            db, owner, "openai_compat", {"base_url": BASE, "model": "llama-x", "api_key": KEY, "context_tokens": 1024}
        )


async def test_an_agent_can_opt_out_of_the_model_written_summary(ort, sessions, owner, endpoint, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_CAPTURE_QUIET_SECONDS", 0)
    await _save_window(sessions, owner)
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="quiet", provider="openai_compat", compaction_summary=False)
    asked = []
    chat = endpoint.handler

    def handler(request):
        if json.loads(request.content).get("stream") is False:
            asked.append(1)
            return httpx.Response(200, json={"choices": [{"message": {"content": "A summary."}}]})
        return chat(request)

    endpoint.handler = handler
    thread = None
    for n in range(1, 9):
        thread, events = await run(ort, sessions, f"message {n} {'x' * 600}", count=n, thread=thread, agent_id=agent.id)
    compactions = data_of(events, "context.compacted")
    assert compactions and asked == [] and {c["method"] for c in compactions} == {"excerpts"}


async def test_the_environment_switch_turns_it_off_for_everyone(ort, sessions, owner, endpoint, monkeypatch):
    prompts, _, compactions = await _fold_with_model(ort, sessions, owner, endpoint, monkeypatch, "A summary.")
    assert compactions[0]["method"] == "model"
    monkeypatch.setattr(settings, "COMPACTION_MODEL_SUMMARY", False)
    before = len(prompts)
    thread = None
    for n in range(1, 9):
        thread, events = await run(ort, sessions, f"again {n} {'x' * 600}", count=n, thread=thread)
    assert len(prompts) == before and {c["method"] for c in data_of(events, "context.compacted")} == {"excerpts"}


def _plan():
    items = [{"role": "user" if n % 2 == 0 else "assistant", "text": f"line {n}", "turn_id": n} for n in range(10)]
    folded, kept = items[:6], items[6:]
    return context_compaction.Plan(
        items=kept,
        record={"summary": "x", "engine": "openai_compat", "tokens_after": 1},
        folded=folded, kept=kept, previous=None, room=context_compaction.SUMMARY_MAX_CHARS,
    )


async def test_a_slow_summary_call_is_cut_at_the_timeout_and_the_excerpts_stand(monkeypatch):
    monkeypatch.setattr(context_compaction, "MODEL_SUMMARY_TIMEOUT_SECONDS", 0.05)
    cancelled = []

    async def slow(prompt):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    assert await context_compaction.model_summary(_plan(), slow) is None
    assert cancelled == [True]


async def test_a_stop_cancels_the_summary_and_a_late_answer_is_never_applied():
    aborted = asyncio.Event()
    started = asyncio.Event()

    async def stubborn(prompt):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            return "A summary that arrived after the stop."  # ignores the cancellation

    task = asyncio.ensure_future(context_compaction.model_summary(_plan(), stubborn, aborted=aborted))
    await started.wait()
    aborted.set()
    with pytest.raises(ReplayAborted):
        await task
    with pytest.raises(ReplayAborted):  # already stopped: nothing is even asked
        await context_compaction.model_summary(_plan(), stubborn, aborted=aborted)


async def test_any_failure_or_an_empty_answer_keeps_the_excerpts():
    async def boom(prompt):
        raise ValueError("anything at all")

    async def blank(prompt):
        return "  \n "

    assert await context_compaction.model_summary(_plan(), boom) is None
    assert await context_compaction.model_summary(_plan(), blank) is None


async def test_stopping_a_turn_during_the_summary_ends_it_without_sending_or_storing(ort, sessions, owner, endpoint, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_CAPTURE_QUIET_SECONDS", 0)
    await _save_window(sessions, owner)
    gate, summarizing = asyncio.Event(), asyncio.Event()
    chat_requests = []
    chat = endpoint.handler

    async def handler(request):
        if json.loads(request.content).get("stream") is False:
            summarizing.set()
            await gate.wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": "Too late."}}]})
        chat_requests.append(request)
        return chat(request)

    endpoint.handler = handler
    thread = await ort.create_thread(provider="openai_compat")
    count = 0
    while not summarizing.is_set():
        count += 1
        assert count < 20, "the conversation never reached the fold"
        await ort.send_turn(thread.id, f"message {count} {'x' * 600}")
        waiter = asyncio.ensure_future(summarizing.wait())
        done = asyncio.ensure_future(finished(sessions, thread.id, count=count))
        await asyncio.wait({waiter, done}, return_when=asyncio.FIRST_COMPLETED, timeout=20)
        waiter.cancel()
    sent_before = len(chat_requests)
    await ort.stop_turn(thread.id)
    await finished(sessions, thread.id, count=count)
    gate.set()
    events = await events_of(sessions, thread.id)
    assert data_of(events, "turn.cancelled")
    last_turn = max(e.turn_id for e in events)
    assert [e for e in events if e.turn_id == last_turn and e.type == "context.compacted"] == []
    assert len(chat_requests) == sent_before  # nothing was sent to the endpoint for the stopped turn
