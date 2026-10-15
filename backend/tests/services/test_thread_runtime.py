"""Durable thread turns end to end (F04, slice 2).

A real child process plays the Claude CLI (tests/services/fake_claude_cli.py), so
the pipes, the stream parser, the adapter, the recorder, the approvals and the
database are all the real code. Rows are committed from separate connections, so
the concurrency and ownership behaviour is the real one. Runs on whichever
database the suite uses (SQLite first)."""

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.db.engine import create_app_async_engine
from app.db.models import Approval, Event, Thread, Turn
from app.services import local_claude_runner, thread_runtime
from app.services import thread_store as store
from app.services.claude_adapter import ClaudeAdapter
from app.services.provider_adapter import ProviderStatus
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import (
    ProviderUnavailable,
    SessionBroken,
    ThreadRuntime,
    UnknownProvider,
)

FAKE_CLI = Path(__file__).with_name("fake_claude_cli.py")
TERMINAL = {"turn.completed", "turn.failed", "turn.cancelled", "turn.interrupted"}
TOOL_INPUT = {"file_path": "notes.txt", "content": "hello"}


@pytest.fixture
async def sessions():
    engine = create_app_async_engine()
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def owner():
    return "od_rt_" + uuid4().hex


@pytest.fixture(autouse=True)
async def cleanup(sessions, owner):
    yield
    async with sessions() as db:
        ids = select(Thread.id).where(Thread.owner_id == owner)
        await db.execute(delete(Approval).where(Approval.thread_id.in_(ids)))
        await db.execute(delete(Event).where(Event.thread_id.in_(ids)))
        await db.execute(delete(Turn).where(Turn.thread_id.in_(ids)))
        await db.execute(delete(Thread).where(Thread.owner_id == owner))
        await db.commit()


class Cli:
    """What the test sees of the fake CLI: set its script, read what it logged."""

    def __init__(self, plan: Path) -> None:
        self.plan = plan
        self.script()

    def script(self, *steps: dict) -> None:
        self.plan.write_text(json.dumps({"steps": list(steps) or [{"type": "chat"}]}))

    def log(self, event: str | None = None) -> list[dict]:
        path = self.plan.parent / "log.jsonl"
        if not path.exists():
            return []
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [e for e in entries if event is None or e["event"] == event]

    def launches(self) -> list[tuple[str, str]]:
        return [(e["mode"], e["sid"]) for e in self.log("start")]


@pytest.fixture
def cli(monkeypatch, tmp_path):
    fake = Cli(tmp_path / "plan.json")
    monkeypatch.setattr(local_claude_runner, "resolve_claude_executable", lambda: tmp_path / "claude")
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)

    def fake_build_args(executable, session_id, tools=False, resume=False):
        return [
            sys.executable, str(FAKE_CLI), str(fake.plan),
            "resume" if resume else "new", session_id, "1" if tools else "0",
        ]

    monkeypatch.setattr(local_claude_runner, "build_args", fake_build_args)
    return fake


class ReadyClaude(ClaudeAdapter):
    async def status(self) -> ProviderStatus:
        return ProviderStatus(ready=True, version="fake")


@pytest.fixture
async def rt(sessions, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(thread_runtime, "APPROVAL_POLL_SECONDS", 0.1)
    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"claude": ReadyClaude()}, status_ttl=0, _session_factory=sessions
    )
    yield runtime
    await runtime.shutdown()  # never leave a fake CLI running


@pytest.fixture
def tools_on(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)


async def wait_until(check, timeout=20.0):
    """Poll `check()` (sync or async) until it returns something truthy."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = check()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.05)


async def new_thread(sessions, owner, **kwargs):
    kwargs.setdefault("provider", "claude")
    kwargs.setdefault("provider_session_id", uuid4().hex)
    async with sessions() as db:
        return await store.create_thread(db, owner_id=owner, **kwargs)


async def events_of(sessions, thread_id):
    async with sessions() as db:
        return await store.list_all_events(db, thread_id)


async def types_of(sessions, thread_id):
    return [e.type for e in await events_of(sessions, thread_id)]


async def finished(sessions, thread_id, count=1):
    """Wait until `count` turns of the thread have a terminal event."""

    async def look():
        done = [t for t in await types_of(sessions, thread_id) if t in TERMINAL]
        return len(done) >= count

    await wait_until(look)


async def reload(sessions, model, key):
    async with sessions() as db:
        return await db.get(model, key)


async def pending_approval(sessions, thread_id):
    async def look():
        async with sessions() as db:
            items = await store.list_approvals(db, thread_id, status="pending")
        return items[0] if items else None

    return await wait_until(look)


async def interrupt_like_another_process(sessions, turn_id):
    """What another process's lease sweep does to a turn whose heartbeat is old."""
    async with sessions() as db:
        return await store.interrupt_turn(db, turn_id, stale_before=store.utcnow() + timedelta(minutes=1))


# --- the happy path ----------------------------------------------------------------


async def test_two_turns_continue_one_provider_session(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "chat", "text": "Hello world"}, {"type": "chat", "text": "Second answer"})

    first = await rt.send_turn(thread.id, "hi there")
    await finished(sessions, thread.id, 1)
    assert await types_of(sessions, thread.id) == ["turn.started", "assistant.message", "turn.completed"]
    done = await reload(sessions, Turn, first.id)
    assert (done.status, done.result, done.model, done.error) == ("succeeded", "Hello world", "fake-model", None)
    assert done.usage == {"input_tokens": 3, "output_tokens": 2, "cost_usd": 0.001}
    assert (await reload(sessions, Thread, thread.id)).session_state == "active"

    second = await rt.send_turn(thread.id, "and again")
    await finished(sessions, thread.id, 2)
    assert second.seq == 2

    # The first turn created the session, the second continued the same one.
    assert cli.launches() == [("new", thread.provider_session_id), ("resume", thread.provider_session_id)]
    events = await events_of(sessions, thread.id)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    history = store.history_items(events)
    assert [(h["role"], h["text"]) for h in history] == [
        ("user", "hi there"), ("assistant", "Hello world"),
        ("user", "and again"), ("assistant", "Second answer"),
    ]
    assert rt._runs == {}


async def test_chat_only_runs_send_the_prompt_as_plain_text_and_use_no_tools(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    await rt.send_turn(thread.id, "just talk")
    await finished(sessions, thread.id)
    [start] = cli.log("start")
    assert start["tools"] is False
    assert cli.log("prompt")[0]["prompt"] == "just talk"


async def test_each_message_of_a_turn_is_stored_in_order(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "two_messages"})
    await rt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    messages = [e.data["text"] for e in await events_of(sessions, thread.id) if e.type == "assistant.message"]
    assert messages == ["Let me look.", "All done."]


async def test_typing_deltas_reach_listeners_but_are_never_stored(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    subscription = rt.hub.subscribe(thread.id)
    await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)
    assert "".join(d.text for d in subscription.drain_deltas()) == "Hello world"
    assert subscription.wake.is_set()  # woken after commits, to read the database
    assert rt.hub.subscribe(thread.id).snapshot == ""  # nothing is typing any more
    assert "text.delta" not in await types_of(sessions, thread.id)


async def test_a_listener_joining_mid_message_gets_the_text_so_far(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "hang"})
    await rt.send_turn(thread.id, "hello")
    await wait_until(lambda: rt.hub.subscribe(thread.id).snapshot == "typing")


# --- tools and durable approvals -------------------------------------------------------


async def test_an_allowed_tool_call_runs_with_exactly_the_input_that_was_shown(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool"})
    turn = await rt.send_turn(thread.id, "write a note")

    approval = await pending_approval(sessions, thread.id)
    assert (approval.tool, approval.kind, approval.input, approval.summary) == (
        "Write", "file_write", TOOL_INPUT, "notes.txt"
    )
    assert cli.log("reply") == []  # nothing runs until a person answers
    assert await rt.answer_approval(approval.id, "allow")
    await finished(sessions, thread.id)

    [reply] = cli.log("reply")
    assert reply["reply"]["response"]["response"] == {"behavior": "allow", "updatedInput": TOOL_INPUT}
    assert await types_of(sessions, thread.id) == [
        "turn.started", "tool.started", "approval.requested", "approval.resolved",
        "tool.completed", "turn.completed",
    ]
    events = {e.type: e.data for e in await events_of(sessions, thread.id)}
    assert events["tool.started"]["input"] == TOOL_INPUT and events["tool.started"]["call_id"] == "tu1"
    assert events["tool.completed"] == {"call_id": "tu1", "ok": True, "output": "File created", "truncated": False}
    stored = await reload(sessions, Approval, approval.id)
    assert (stored.status, stored.delivered_at is not None) == ("allowed", True)
    assert (await reload(sessions, Turn, turn.id)).result == "finished: allow"


async def test_a_denied_tool_call_is_refused_and_claude_carries_on(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool"})
    await rt.send_turn(thread.id, "write a note")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    assert cli.log("reply")[0]["reply"]["response"]["response"]["behavior"] == "deny"
    stored = await reload(sessions, Approval, approval.id)
    assert (stored.status, stored.delivered_at) == ("denied", None)
    assert (await types_of(sessions, thread.id))[-1] == "turn.completed"


async def test_an_unanswered_approval_expires_and_is_refused(rt, sessions, cli, tools_on, monkeypatch, owner):
    monkeypatch.setattr(settings, "LOCAL_APPROVAL_TIMEOUT_SECONDS", 1)
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool"})
    await rt.send_turn(thread.id, "write a note")
    await finished(sessions, thread.id)
    reply = cli.log("reply")[0]["reply"]["response"]["response"]
    assert reply["behavior"] == "deny" and "Nobody answered" in reply["message"]
    approval = (await _approvals(sessions, thread.id))[0]
    assert approval.status == "expired"
    assert not await rt.answer_approval(approval.id, "allow")  # too late: nothing changes


async def _approvals(sessions, thread_id):
    async with sessions() as db:
        return await store.list_approvals(db, thread_id)


async def test_a_call_too_big_to_review_is_refused_without_asking(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool_big"})
    await rt.send_turn(thread.id, "write a lot")
    await finished(sessions, thread.id)
    reply = cli.log("reply")[0]["reply"]["response"]["response"]
    assert reply["behavior"] == "deny" and "too large" in reply["message"]
    assert await _approvals(sessions, thread.id) == []


async def test_a_second_message_is_refused_while_a_turn_runs(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool"})
    await rt.send_turn(thread.id, "first")
    approval = await pending_approval(sessions, thread.id)
    with pytest.raises(store.ThreadBusy):
        await rt.send_turn(thread.id, "second")
    await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    assert [t.prompt_text for t in await _turns(sessions, thread.id)] == ["first"]


async def _turns(sessions, thread_id):
    async with sessions() as db:
        return await store.list_turns(db, thread_id)


async def test_answering_an_unknown_or_bad_approval_changes_nothing(rt):
    assert not await rt.answer_approval("no-such-approval", "allow")
    with pytest.raises(ValueError):
        await rt.answer_approval("x", "maybe")


# --- losing the turn ----------------------------------------------------------------------


async def test_a_run_whose_turn_was_taken_away_kills_its_child_and_writes_nothing(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "hang"})
    turn = await rt.send_turn(thread.id, "work")
    await wait_until(lambda: cli.log("hanging"))

    assert await interrupt_like_another_process(sessions, turn.id)
    await wait_until(lambda: rt._runs == {})  # the next heartbeat fails and the CLI is killed

    assert await types_of(sessions, thread.id) == ["turn.started", "turn.interrupted"]
    assert (await reload(sessions, Turn, turn.id)).status == "interrupted"
    # The session may exist but the run could not say so: the state is left as it was.
    assert (await reload(sessions, Thread, thread.id)).session_state == "new"


async def test_every_event_a_run_writes_checks_that_it_still_owns_the_turn(rt, sessions, cli, monkeypatch, owner):
    """With the heartbeat out of the picture, the write itself must refuse: a run that
    lost its turn (a suspended process waking up) cannot add so much as one event."""
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 3600)
    thread = await new_thread(sessions, owner)
    cli.script({"type": "hang"})
    turn = await rt.send_turn(thread.id, "work")
    await wait_until(lambda: cli.log("hanging"))
    run = rt._runs[turn.id]

    assert await interrupt_like_another_process(sessions, turn.id)
    assert await run.recorder.emit("assistant.message", {"text": "too late"}) is False

    assert await types_of(sessions, thread.id) == ["turn.started", "turn.interrupted"]
    assert run.recorder.lost and run.ctx.aborted  # and it stopped its child
    await wait_until(lambda: rt._runs == {})


async def test_an_allow_is_not_delivered_when_the_turn_is_lost_just_before(rt, sessions, cli, tools_on, monkeypatch, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool"})
    turn = await rt.send_turn(thread.id, "write a note")
    approval = await pending_approval(sessions, thread.id)

    real = store.mark_allow_delivered

    async def sweep_wins_first(db, approval_id, owner_token):
        await interrupt_like_another_process(sessions, turn.id)  # the sweep gets in between
        return await real(db, approval_id, owner_token)

    monkeypatch.setattr(store, "mark_allow_delivered", sweep_wins_first)
    assert await rt.answer_approval(approval.id, "allow")  # the person did say yes
    await wait_until(lambda: rt._runs == {})

    assert not [r for r in cli.log("reply") if r["reply"]["response"]["response"]["behavior"] == "allow"]
    stored = await reload(sessions, Approval, approval.id)
    assert (stored.status, stored.delivered_at) == ("cancelled", None)
    types = await types_of(sessions, thread.id)
    assert "tool.completed" not in types and "turn.completed" not in types
    assert types[-1] == "turn.interrupted"


async def test_an_answer_after_the_turn_was_taken_away_is_refused(rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool"})
    turn = await rt.send_turn(thread.id, "write a note")
    approval = await pending_approval(sessions, thread.id)
    await interrupt_like_another_process(sessions, turn.id)  # cancels the open approval
    assert not await rt.answer_approval(approval.id, "allow")
    await wait_until(lambda: rt._runs == {})
    assert not [r for r in cli.log("reply") if r["reply"]["response"]["response"]["behavior"] == "allow"]


# --- sessions ------------------------------------------------------------------------------


async def test_a_failed_resume_breaks_the_thread_and_never_starts_a_new_session(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner, session_state="active")  # claims a session the CLI lost
    turn = await rt.send_turn(thread.id, "continue please")
    await finished(sessions, thread.id)

    failed = await reload(sessions, Turn, turn.id)
    assert (failed.status, failed.error_code) == ("failed", "session_resume_failed")
    assert "Start a new session" in failed.error
    assert (await reload(sessions, Thread, thread.id)).session_state == "broken"
    assert await types_of(sessions, thread.id) == ["turn.started", "turn.failed"]
    assert cli.launches() == [("resume", thread.provider_session_id)]  # no fresh session was tried

    with pytest.raises(SessionBroken):
        await rt.send_turn(thread.id, "try again")
    assert len(await _turns(sessions, thread.id)) == 1  # refused before a turn was created


async def test_a_turn_cut_off_after_the_session_exists_is_continued_with_resume(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "hang"}, {"type": "chat", "text": "Back again"})
    first = await rt.send_turn(thread.id, "start something")
    await wait_until(lambda: cli.log("hanging"))
    await rt.shutdown()  # cut off: the session exists but the thread never learned it
    assert (await reload(sessions, Turn, first.id)).status == "interrupted"
    assert (await reload(sessions, Thread, thread.id)).session_state == "new"

    second = await rt.send_turn(thread.id, "carry on")
    await finished(sessions, thread.id, 2)

    # --session-id was refused as "already in use", so the same turn was retried
    # with --resume, once. Nothing was lost and no new session was made.
    sid = thread.provider_session_id
    assert cli.launches() == [("new", sid), ("new", sid), ("resume", sid)]
    assert len(cli.log("already_in_use")) == 1
    done = await reload(sessions, Turn, second.id)
    assert (done.status, done.result) == ("succeeded", "Back again")
    assert (await reload(sessions, Thread, thread.id)).session_state == "active"


# --- failures ------------------------------------------------------------------------------


async def test_a_failed_turn_keeps_the_partial_answer_marked_incomplete(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "partial_fail"})
    turn = await rt.send_turn(thread.id, "say something long")
    await finished(sessions, thread.id)

    events = await events_of(sessions, thread.id)
    assert [e.type for e in events] == ["turn.started", "assistant.message", "turn.failed"]
    assert events[1].data == {"text": "partial text", "incomplete": True}
    assert events[2].data["error_code"] == "cli_error" and "crashed" in events[2].data["error"]
    failed = await reload(sessions, Turn, turn.id)
    assert (failed.status, failed.result, failed.error_code) == ("failed", "partial text", "cli_error")
    history = store.history_items(events)
    assert history[1]["incomplete"] is True


async def test_an_error_result_from_claude_is_a_failed_turn(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "error_result"})
    turn = await rt.send_turn(thread.id, "hello")
    await finished(sessions, thread.id)
    failed = await reload(sessions, Turn, turn.id)
    assert (failed.status, failed.error, failed.error_code) == ("failed", "model overloaded", "claude_error")


async def test_a_provider_that_is_not_ready_starts_no_turn(sessions, owner, cli):
    class NotReady(ClaudeAdapter):
        async def status(self):
            return ProviderStatus(ready=False, error="Claude is not signed in.")

    runtime = ThreadRuntime(hub=ThreadHub(), adapters={"claude": NotReady()}, status_ttl=0, _session_factory=sessions)
    thread = await new_thread(sessions, owner)
    with pytest.raises(ProviderUnavailable, match="not signed in"):
        await runtime.send_turn(thread.id, "hello")
    assert await _turns(sessions, thread.id) == []


async def test_a_thread_for_an_unregistered_provider_or_a_missing_thread_is_refused(rt, sessions, owner):
    thread = await new_thread(sessions, owner, provider="codex", session_state="none", provider_session_id=None)
    with pytest.raises(UnknownProvider):
        await rt.send_turn(thread.id, "hello")
    with pytest.raises(store.ThreadNotFound):
        await rt.send_turn(987654321, "hello")


# --- recovery and shutdown -------------------------------------------------------------------


async def test_shutdown_interrupts_this_processs_own_turns_at_once(rt, sessions, cli, owner):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "hang"})
    turn = await rt.send_turn(thread.id, "work")
    await wait_until(lambda: cli.log("hanging"))

    await rt.shutdown()

    assert rt._runs == {}
    assert (await reload(sessions, Turn, turn.id)).status == "interrupted"
    events = await events_of(sessions, thread.id)
    assert [e.type for e in events] == ["turn.started", "turn.interrupted"]
    assert events[1].data == {"reason": "shutdown"}
    async with sessions() as db:  # no 60-second wait: the thread can take a new turn
        await store.start_turn(db, thread.id, "next")


async def test_the_sweep_interrupts_a_dead_turn_and_wakes_listeners(rt, sessions, owner):
    thread = await new_thread(sessions, owner)
    async with sessions() as db:
        turn = await store.start_turn(db, thread.id, "work", owner_instance="a-process-that-died")
    async with sessions() as db:
        from sqlalchemy import update

        await db.execute(update(Turn).where(Turn.id == turn.id).values(
            heartbeat_at=store.utcnow() - timedelta(seconds=settings.THREAD_LEASE_SECONDS + 30)))
        await db.commit()
    subscription = rt.hub.subscribe(thread.id)

    assert turn.id in await rt.sweep_stale()

    assert await types_of(sessions, thread.id) == ["turn.started", "turn.interrupted"]  # no invented answer
    assert subscription.wake.is_set()
    assert turn.id not in await rt.sweep_stale()  # once only
