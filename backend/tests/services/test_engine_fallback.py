"""Engine limits and the quota-aware fallback (app/services/engine_errors.py,
ThreadRuntime._after_limit): a usage/rate limit or lack of credits is
classified for every engine, the engine is marked limited (GET /engines shows
it), and an agent's fallback engine takes a turn only when its engine refused
the turn before starting on it and the fallback can do what the turn needs,
with a visible `engine.switched` event. Real runtime and database; scripted
engines."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core.config import settings
from app.db.models import Agent, Turn
from app.services import agent_store, engine_errors
from app.services import thread_store as store
from app.services.local_claude_stream import StreamNormalizer
from app.services.provider_adapter import Capabilities, ModelOption, ProviderStatus, TurnOutcome
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.thread_harness import (  # noqa: F401
    cleanup,
    events_of,
    finished,
    owner,
    reload,
    sessions,
    types_of,
)

LIMIT_TEXT = "You've hit your usage limit · try again in 2 hours"
FULL = Capabilities(tools=True, approvals=True, mcp=True, images=True, resume=True, computers=False)


class Scripted:
    """An engine that plays one behaviour per turn and records what it got."""

    kind = "cli"
    models: list[ModelOption] = []
    custom_models = True

    def __init__(self, key, label, behaviour, *, caps=FULL, sessions=False, ready=True):
        self.key, self.label, self.behaviour = key, label, behaviour
        self.capabilities = caps
        self.sessions = sessions
        self.ready = ready
        self.calls = []

    def new_session_id(self):
        return uuid4().hex if self.sessions else None

    async def status(self):
        return ProviderStatus(ready=self.ready, error=None if self.ready else "not signed in")

    async def run_turn(self, ctx, sink, gate):
        self.calls.append(ctx)
        return await self.behaviour(ctx, sink, gate)


async def limited_before(ctx, sink, gate):
    return TurnOutcome("failed", error=LIMIT_TEXT, error_code="cli_error")


async def limited_after(ctx, sink, gate):
    await sink.emit("assistant.message", {"text": "Starting on it."})
    return TurnOutcome("failed", error=LIMIT_TEXT, error_code="cli_error")


async def replies(ctx, sink, gate):
    await sink.emit("assistant.message", {"text": f"answer from {ctx.model or 'default'}"})
    return TurnOutcome("succeeded", result="done", model=ctx.model, session_state="active",
                       provider_state={"not": "kept"})


async def times_out(ctx, sink, gate):
    return TurnOutcome("failed", error="usage limit words in a timeout", error_code="timeout")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)


@pytest.fixture(autouse=True)
async def agent_cleanup(sessions, owner):
    yield
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


@pytest.fixture
def make_rt(sessions, owner, tmp_path):
    made = []

    def make(**adapters):
        runtime = ThreadRuntime(hub=ThreadHub(), adapters=adapters, status_ttl=0, _session_factory=sessions, owner_id=owner)
        made.append(runtime)
        return runtime

    yield make


async def conversation(rt, sessions, owner, tmp_path, *, provider="primary", fallback="backup", model="backup-large"):
    async with sessions() as db:
        agent = await agent_store.create_agent(
            db, owner_id=owner, name="Agent " + uuid4().hex[:6], provider=provider,
            workspace_path=str(tmp_path), fallback_provider=fallback, fallback_model=model if fallback else None,
        )
    return await rt.create_thread(agent_id=agent.id)


async def last_turn(sessions, thread_id):
    async with sessions() as db:
        turns = await store.list_turns(db, thread_id)
    return turns[-1]


# --- classification -----------------------------------------------------------------------------


@pytest.mark.parametrize("text,kind", [
    ("Claude AI usage limit reached|4102444800", engine_errors.USAGE_LIMIT),
    ("You've hit your weekly limit · resets 3pm", engine_errors.USAGE_LIMIT),
    ("You've hit your usage limit. Upgrade to Pro or try again in 3 days 2 hours.", engine_errors.USAGE_LIMIT),
    ("The endpoint answered 429: Rate limit reached for gpt-4o. Please try again in 20s.", engine_errors.RATE_LIMIT),
    ("exceeded retry limit, last status: 429 Too Many Requests", engine_errors.RATE_LIMIT),
    ("The endpoint answered 429: You exceeded your current quota, please check your plan.", engine_errors.CREDITS),
    ("Credit balance is too low", engine_errors.CREDITS),
])
def test_limits_are_recognised(text, kind):
    limit = engine_errors.classify(text)
    assert limit is not None and limit.kind == kind


@pytest.mark.parametrize("text", ["Timed out after 600 seconds", "model not found", "", None, "Claude exited with code 1"])
def test_other_failures_are_not_limits(text):
    assert engine_errors.classify(text) is None


def test_reset_times_are_read_when_given():
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    assert engine_errors.classify("Claude AI usage limit reached|4102444800", now=now).resets_at == datetime.fromtimestamp(4102444800, UTC)
    assert engine_errors.classify("429 rate limit. Please try again in 20s.", now=now).resets_at == now + timedelta(seconds=20)
    assert engine_errors.classify(LIMIT_TEXT, now=now).resets_at == now + timedelta(hours=2)
    assert engine_errors.classify("usage limit reached", resets_at=4102444800, now=now).resets_at.year == 2100
    assert engine_errors.classify("You've hit your weekly limit", now=now).resets_at is None


def test_claude_limit_frames_are_read_and_never_shown_as_the_answer():
    n = StreamNormalizer(max_text_bytes=10_000)
    n.feed('{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":4102444800,"rateLimitType":"five_hour"}}')
    chunks = n.feed('{"type":"assistant","error":"rate_limit","message":{"id":"x","content":[{"type":"text","text":"You\'ve hit your session limit"}]}}')
    n.feed('{"type":"result","is_error":true,"result":"You\'ve hit your session limit"}')
    assert chunks == [] and n.take_messages() == []
    assert n.limit_refused and n.limit_resets_at == 4102444800 and n.limit_window == "five_hour"
    assert n.api_error == "You've hit your session limit"
    # Allowed, or running on extra usage: not a refusal.
    ok = StreamNormalizer(max_text_bytes=10_000)
    ok.feed('{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","isUsingOverage":true}}')
    ok.feed('{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning"}}')
    assert not ok.limit_refused


# --- the fallback --------------------------------------------------------------------------------


async def test_a_limit_before_the_engine_started_moves_the_turn_to_the_fallback(make_rt, sessions, owner, tmp_path):
    primary, backup = Scripted("primary", "Primary", limited_before), Scripted("backup", "Backup", replies)
    rt = make_rt(primary=primary, backup=backup)
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)

    turn = await last_turn(sessions, thread.id)
    assert turn.status == "succeeded" and turn.model == "backup-large"
    events = await events_of(sessions, thread.id)
    types = [e.type for e in events]
    assert types.index("engine.switched") < types.index("assistant.message")
    switched = next(e for e in events if e.type == "engine.switched").data
    assert switched["from"] == "primary" and switched["to"] == "backup" and switched["model"] == "backup-large"
    assert switched["reason"] == "usage_limit" and switched["resets_at"]
    assert switched["message"].startswith(
        "Switched this turn to Backup because Primary refused it before starting: Primary has reached its usage limit"
    )
    assert backup.calls[0].prompt == "hello" and backup.calls[0].resume is False
    # The conversation stays on its own engine and keeps none of the fallback's state.
    reloaded = await rt.engines.describe()
    limit = next(e for e in reloaded if e["id"] == "primary")["limit"]
    assert limit["kind"] == "usage_limit" and limit["resets_at"]
    assert next(e for e in reloaded if e["id"] == "backup")["limit"] is None
    async with sessions() as db:
        current = await store.get_thread(db, thread.id)
    assert current.provider == "primary" and current.provider_state is None


async def test_a_limit_after_the_engine_started_is_never_moved(make_rt, sessions, owner, tmp_path):
    primary, backup = Scripted("primary", "Primary", limited_after), Scripted("backup", "Backup", replies)
    rt = make_rt(primary=primary, backup=backup)
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)

    turn = await last_turn(sessions, thread.id)
    assert turn.status == "failed" and turn.error_code == "engine_limited"
    assert "already started on this message" in turn.error and "may have run tools" in turn.error
    assert not backup.calls
    assert "engine.switched" not in await types_of(sessions, thread.id)
    assert rt.engine_limit("primary")["kind"] == "usage_limit"


async def test_a_fallback_without_a_capability_the_turn_needs_is_refused(make_rt, sessions, owner, tmp_path):
    primary = Scripted("primary", "Primary", limited_before)
    backup = Scripted("backup", "Backup", replies, caps=Capabilities(tools=True, approvals=False))
    rt = make_rt(primary=primary, backup=backup)
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)

    turn = await last_turn(sessions, thread.id)
    assert turn.error_code == "engine_limited"
    assert "fallback engine, Backup, cannot take this turn: it lacks approvals" in turn.error
    assert "Wait for the limit to reset" in turn.error
    assert not backup.calls


async def test_a_continuing_conversation_is_not_moved_to_an_engine_that_cannot_read_it(make_rt, sessions, owner, tmp_path):
    first = {"n": 0}

    async def ok_then_limited(ctx, sink, gate):
        first["n"] += 1
        return await (replies if first["n"] == 1 else limited_before)(ctx, sink, gate)

    primary = Scripted("primary", "Primary", ok_then_limited)
    backup = Scripted("backup", "Backup", replies, sessions=True)  # keeps a session of its own
    rt = make_rt(primary=primary, backup=backup)
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "one")
    await finished(sessions, thread.id)
    await rt.send_turn(thread.id, "two")
    await finished(sessions, thread.id, 2)

    turn = await last_turn(sessions, thread.id)
    assert turn.error_code == "engine_limited" and "earlier messages" in turn.error
    assert not backup.calls


async def test_a_continuing_conversation_moves_to_an_engine_that_replays_the_transcript(make_rt, sessions, owner, tmp_path):
    first = {"n": 0}

    async def ok_then_limited(ctx, sink, gate):
        first["n"] += 1
        return await (replies if first["n"] == 1 else limited_before)(ctx, sink, gate)

    backup = Scripted("backup", "Backup", replies)
    rt = make_rt(primary=Scripted("primary", "Primary", ok_then_limited), backup=backup)
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "one")
    await finished(sessions, thread.id)
    await rt.send_turn(thread.id, "two")
    await finished(sessions, thread.id, 2)

    assert (await last_turn(sessions, thread.id)).status == "succeeded"
    history = await backup.calls[0].history()
    assert any("one" in str(item) for item in history)


async def test_no_fallback_fails_with_the_limit_and_a_next_step(make_rt, sessions, owner, tmp_path):
    rt = make_rt(primary=Scripted("primary", "Primary", limited_before))
    thread = await conversation(rt, sessions, owner, tmp_path, fallback=None)
    await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)
    turn = await last_turn(sessions, thread.id)
    assert turn.error_code == "engine_limited"
    assert turn.error.startswith("Primary has reached its usage limit")
    assert "give it a fallback engine" in turn.error


async def test_fallback_history_uses_its_own_compaction_budget(make_rt, sessions, owner, tmp_path):
    async def replay(ctx, sink, gate):
        await ctx.history()
        return await replies(ctx, sink, gate)

    backup = Scripted("backup", "Backup", replay)
    backup.replays_history = True
    backup.compact_at_tokens = 512
    primary = Scripted("primary", "Primary", replies)
    rt = make_rt(primary=primary, backup=backup)
    thread = await conversation(rt, sessions, owner, tmp_path)
    for n in range(6):
        await rt.send_turn(thread.id, f"Earlier message {n}: " + "history " * 150)
        await finished(sessions, thread.id, n + 1)
    primary.behaviour = limited_before
    await rt.send_turn(thread.id, "Continue on the fallback")
    await finished(sessions, thread.id, 7)
    assert (await last_turn(sessions, thread.id)).status == "succeeded"
    records = [e.data for e in await events_of(sessions, thread.id) if e.type == "context.compacted"]
    assert len(records) == 1
    assert records[0]["engine"] == "backup"
    assert records[0]["budget_tokens"] == 512


async def test_return_to_a_native_engine_restores_fallback_context_after_restart(make_rt, sessions, owner, tmp_path):
    primary = Scripted("primary", "Primary", limited_before, sessions=True)
    backup = Scripted("backup", "Backup", replies)
    rt = make_rt(primary=primary, backup=backup)
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "Remember the project is called Orchard")
    await finished(sessions, thread.id)
    # A replacement runtime must derive the handoff from durable events.
    primary.behaviour = replies
    rt = make_rt(primary=primary, backup=backup)
    await rt.send_turn(thread.id, "What is the project called?")
    await finished(sessions, thread.id, 2)
    prompt = primary.calls[-1].prompt
    assert "project is called Orchard" in prompt
    assert "answer from backup-large" in prompt
    assert "Do not repeat their requests or tool calls" in prompt
    assert prompt.endswith("What is the project called?")
    started = [e.data for e in await events_of(sessions, thread.id) if e.type == "turn.started"]
    assert started[-1]["prompt_text"] == "What is the project called?"
    await rt.send_turn(thread.id, "One more")
    await finished(sessions, thread.id, 3)
    assert primary.calls[-1].prompt == "One more"


async def test_session_reset_discards_pending_fallback_handoff(make_rt, sessions, owner, tmp_path):
    primary = Scripted("primary", "Primary", limited_before, sessions=True)
    rt = make_rt(primary=primary, backup=Scripted("backup", "Backup", replies))
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "Old fallback message")
    await finished(sessions, thread.id)
    await rt.reset_session(thread.id)
    primary.behaviour = replies
    await rt.send_turn(thread.id, "Start over")
    await finished(sessions, thread.id, 2)
    assert primary.calls[-1].prompt == "Start over"


async def test_partial_fallback_output_is_handed_back_to_the_native_engine(make_rt, sessions, owner, tmp_path):
    primary = Scripted("primary", "Primary", limited_before, sessions=True)
    rt = make_rt(primary=primary, backup=Scripted("backup", "Backup", limited_after))
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "Work on Orchard")
    await finished(sessions, thread.id)
    assert (await last_turn(sessions, thread.id)).status == "failed"
    primary.behaviour = replies
    await rt.send_turn(thread.id, "Check the partial work")
    await finished(sessions, thread.id, 2)
    assert "Starting on it." in primary.calls[-1].prompt
    assert primary.calls[-1].prompt.endswith("Check the partial work")


async def test_a_fallback_that_is_not_ready_or_limited_too_is_refused(make_rt, sessions, owner, tmp_path):
    backup = Scripted("backup", "Backup", replies, ready=False)
    rt = make_rt(primary=Scripted("primary", "Primary", limited_before), backup=backup)
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)
    assert "it is not ready (not signed in)" in (await last_turn(sessions, thread.id)).error

    backup.ready = True
    rt._note_limit("backup", engine_errors.classify(LIMIT_TEXT), "Backup has reached its usage limit.")
    await rt.send_turn(thread.id, "again")
    await finished(sessions, thread.id, 2)
    assert "it is limited too" in (await last_turn(sessions, thread.id)).error
    assert not backup.calls


async def test_other_failures_and_success_leave_limits_alone_or_clear_them(make_rt, sessions, owner, tmp_path):
    outcomes = iter([times_out, replies])

    async def next_one(ctx, sink, gate):
        return await next(outcomes)(ctx, sink, gate)

    rt = make_rt(primary=Scripted("primary", "Primary", next_one), backup=Scripted("backup", "Backup", replies))
    thread = await conversation(rt, sessions, owner, tmp_path)
    await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)
    turn = await last_turn(sessions, thread.id)
    assert turn.error_code == "timeout" and rt.engine_limit("primary") is None

    rt._note_limit("primary", engine_errors.classify("You've hit your weekly limit"), "limited")
    assert rt.engine_limit("primary") is not None
    await rt.send_turn(thread.id, "again")
    await finished(sessions, thread.id, 2)
    assert rt.engine_limit("primary") is None


def test_limits_expire_at_their_reset_and_rate_limits_after_a_minute(make_rt, monkeypatch):
    rt = make_rt()
    past = engine_errors.EngineLimit(engine_errors.USAGE_LIMIT, datetime.now(UTC) - timedelta(seconds=1), "")
    rt._note_limit("a", past, "x")
    assert rt.engine_limit("a") is None
    rt._note_limit("b", engine_errors.EngineLimit(engine_errors.RATE_LIMIT, None, ""), "x")
    assert rt.engine_limit("b") is not None
    rt._limits["b"] = (rt._limits["b"][0], rt._limits["b"][1] - 61, "x")
    assert rt.engine_limit("b") is None


# --- replay classification -----------------------------------------------------------------------


async def test_a_cut_turn_says_whether_the_engine_had_the_prompt(sessions, owner, tmp_path):
    from tests.services.thread_harness import new_thread

    replays = []
    for steps in ([], ["sent"], ["sent", "work"]):
        thread = await new_thread(sessions, owner, workspace_path=str(tmp_path))
        async with sessions() as db:
            turn = await store.start_turn(db, thread.id, "x")
        if "sent" in steps:
            async with sessions() as db:
                assert await store.mark_engine_sent(db, turn.id, turn.owner_token)
        if "work" in steps:
            async with sessions() as db:
                await store.append_turn_event(db, turn.id, turn.owner_token, "tool.started", {"call_id": "1", "name": "Bash"})
        async with sessions() as db:
            assert await store.interrupt_own_turn(db, turn.id, turn.owner_token)
        events = await events_of(sessions, thread.id)
        replays.append(events[-1].data["replay"])
        assert (await reload(sessions, Turn, turn.id)).status == "interrupted"
    assert replays == ["before_accept", "unknown", "after_accept"]


def test_free_daily_limit_is_usage_and_not_a_short_rate_limit():
    limit = engine_errors.classify("The endpoint answered 429: free-models-per-day limit exceeded")
    assert limit.kind == engine_errors.USAGE_LIMIT
    assert limit.resets_at is None
