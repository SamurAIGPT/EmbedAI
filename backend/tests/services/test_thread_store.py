"""The thread store: durable threads, turns, events and approvals (F04, slice 1).

Runs on whichever database the suite uses (SQLite first). Rows are created with
real committed transactions from separate connections, so the concurrency tests
exercise what the real app will do; each test removes what it created."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.engine import create_app_async_engine
from app.db.models import Approval, Event, Thread, Turn
from app.services import thread_store as store


@pytest.fixture
async def sessions():
    engine = create_app_async_engine()
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def owner():
    return "od_thr_" + uuid4().hex


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


async def call(sessions, fn, *args, **kwargs):
    """Run one store function in its own session, like one request would."""
    async with sessions() as db:
        return await fn(db, *args, **kwargs)


async def new_thread(sessions, owner, **kwargs):
    kwargs.setdefault("provider", "claude")
    kwargs.setdefault("provider_session_id", uuid4().hex)
    return await call(sessions, store.create_thread, owner_id=owner, **kwargs)


async def events_of(sessions, thread_id, after=0):
    return await call(sessions, store.list_events, thread_id, after=after, limit=1000)


async def reload(sessions, model, key):
    async with sessions() as db:
        return await db.get(model, key)


async def make_stale(sessions, turn_id, seconds=120):
    async with sessions() as db:
        await db.execute(
            update(Turn).where(Turn.id == turn_id).values(heartbeat_at=store.utcnow() - timedelta(seconds=seconds))
        )
        await db.commit()


async def running_turn_with_approval(sessions, owner, *, decision=None):
    thread = await new_thread(sessions, owner)
    turn = await call(sessions, store.start_turn, thread.id, "do it")
    approval = await call(
        sessions, store.create_approval,
        thread_id=thread.id, turn_id=turn.id, request_id="req-1", tool="Bash", input={"command": "ls"},
        summary="run ls", kind="command",
    )
    if decision:
        assert await call(sessions, store.decide_approval, approval.id, decision)
    return thread, turn, approval


# --- threads, and provider neutrality ----------------------------------------------


async def test_a_thread_for_any_provider_stores_and_reads_back(sessions, owner):
    claude = await new_thread(sessions, owner, model="sonnet")
    codex = await new_thread(
        sessions, owner, provider="codex", model="gpt-x", session_state="none",
        provider_session_id=None, provider_state={"effort": "high"},
    )
    local = await new_thread(
        sessions, owner, provider="local-openai", model="llama3.1:8b", session_state="none",
        provider_session_id=None,
    )

    got = await reload(sessions, Thread, codex.id)
    assert (got.provider, got.model, got.session_state, got.provider_state) == (
        "codex", "gpt-x", "none", {"effort": "high"}
    )
    assert got.provider_session_id is None and got.last_seq == 0 and got.archived_at is None
    assert got.created_at.tzinfo is not None
    assert (await reload(sessions, Thread, claude.id)).session_state == "new"
    assert (await reload(sessions, Thread, local.id)).provider == "local-openai"


async def test_an_event_type_nobody_defined_yet_needs_no_migration(sessions, owner):
    thread = await new_thread(sessions, owner, provider="codex", session_state="none", provider_session_id=None)
    await call(sessions, store.append_event, thread.id, "codex.plan.updated", {"steps": [1, 2]})
    [event] = await events_of(sessions, thread.id)
    assert (event.type, event.data, event.seq) == ("codex.plan.updated", {"steps": [1, 2]}, 1)


async def test_two_threads_can_never_share_one_provider_session(sessions, owner):
    first = await new_thread(sessions, owner, provider_session_id="shared")
    with pytest.raises(IntegrityError):
        await new_thread(sessions, owner, provider_session_id="shared")
    # The same id under another provider is a different session; threads with no
    # session id are unrestricted.
    await new_thread(sessions, owner, provider="codex", provider_session_id="shared")
    await new_thread(sessions, owner, provider_session_id=None, session_state="none")
    await new_thread(sessions, owner, provider_session_id=None, session_state="none")
    assert first.id


async def test_closed_sets_are_checked_in_code_not_by_the_database(sessions, owner):
    with pytest.raises(ValueError):
        await new_thread(sessions, owner, session_state="bogus")
    with pytest.raises(ValueError):
        await call(sessions, store.create_thread, owner_id=owner, provider=" ")
    thread = await new_thread(sessions, owner)
    turn = await call(sessions, store.start_turn, thread.id, "hi")
    with pytest.raises(ValueError):  # the run may not claim `interrupted`
        await call(sessions, store.finish_turn, turn.id, turn.owner_token, status="interrupted")
    with pytest.raises(ValueError):
        await call(sessions, store.decide_approval, "x", "expired")


async def test_threads_list_newest_activity_first_and_hide_archived(sessions, owner):
    older = await new_thread(sessions, owner, title="older")
    newer = await new_thread(sessions, owner, title="newer")
    await call(sessions, store.append_event, older.id, "note", {})  # activity moves it up
    assert [t.title for t in await call(sessions, store.list_threads, owner)] == ["older", "newer"]
    async with sessions() as db:
        await db.execute(update(Thread).where(Thread.id == newer.id).values(archived_at=store.utcnow()))
        await db.commit()
    assert [t.title for t in await call(sessions, store.list_threads, owner)] == ["older"]
    assert len(await call(sessions, store.list_threads, owner, include_archived=True)) == 2


async def test_session_state_changes_only_from_the_expected_state(sessions, owner):
    thread = await new_thread(sessions, owner)
    assert await call(sessions, store.set_session_state, thread.id, "active", expected="new")
    assert not await call(sessions, store.set_session_state, thread.id, "active", expected="new")
    assert (await reload(sessions, Thread, thread.id)).session_state == "active"


# --- turns ---------------------------------------------------------------------


async def test_starting_a_turn_stores_it_its_event_and_a_title(sessions, owner):
    thread = await new_thread(sessions, owner)
    prompt = "  Summarise   the quarterly report\nplease, " + "x" * 100
    turn = await call(sessions, store.start_turn, thread.id, prompt, owner_instance="proc-1")

    assert (turn.seq, turn.status, turn.owner_instance) == (1, "running", "proc-1")
    assert turn.owner_token and turn.heartbeat_at is not None
    got = await reload(sessions, Thread, thread.id)
    assert got.title == store.make_title(prompt) and len(got.title) <= store.TITLE_MAX_CHARS
    assert got.title.startswith("Summarise the quarterly report please,")
    [event] = await events_of(sessions, thread.id)
    assert (event.seq, event.type, event.turn_id, event.data) == (1, "turn.started", turn.id, {"prompt_text": prompt})


async def test_a_title_is_set_once_and_turn_numbers_count_up(sessions, owner):
    thread = await new_thread(sessions, owner)
    first = await call(sessions, store.start_turn, thread.id, "first prompt")
    assert await call(sessions, store.finish_turn, first.id, first.owner_token, status="succeeded", result="ok")
    second = await call(sessions, store.start_turn, thread.id, "second prompt")
    assert (first.seq, second.seq) == (1, 2)
    assert (await reload(sessions, Thread, thread.id)).title == "first prompt"
    assert [t.seq for t in await call(sessions, store.list_turns, thread.id)] == [1, 2]


async def test_a_second_turn_cannot_start_while_one_is_running(sessions, owner):
    thread = await new_thread(sessions, owner)
    await call(sessions, store.start_turn, thread.id, "one")
    with pytest.raises(store.ThreadBusy):
        await call(sessions, store.start_turn, thread.id, "two")
    assert len(await call(sessions, store.list_turns, thread.id)) == 1
    assert [e.type for e in await events_of(sessions, thread.id)] == ["turn.started"]
    with pytest.raises(store.ThreadNotFound):
        await call(sessions, store.start_turn, 987654321, "x")


async def test_simultaneous_sends_to_one_thread_start_exactly_one_turn(sessions, owner):
    thread = await new_thread(sessions, owner)
    results = await asyncio.gather(
        *(call(sessions, store.start_turn, thread.id, f"send {i}") for i in range(6)),
        return_exceptions=True,
    )
    started = [r for r in results if isinstance(r, Turn)]
    refused = [r for r in results if isinstance(r, store.ThreadBusy)]
    assert len(started) == 1 and len(refused) == 5, results
    assert [e.type for e in await events_of(sessions, thread.id)] == ["turn.started"]


async def test_heartbeat_and_finish_belong_to_the_owner_token_only(sessions, owner):
    thread = await new_thread(sessions, owner)
    turn = await call(sessions, store.start_turn, thread.id, "work")

    assert await call(sessions, store.heartbeat, turn.id, turn.owner_token)
    assert not await call(sessions, store.heartbeat, turn.id, "someone-else")
    assert not await call(
        sessions, store.finish_turn, turn.id, "someone-else", status="succeeded",
        event_type="turn.completed",
    )
    assert [e.type for e in await events_of(sessions, thread.id)] == ["turn.started"]  # nothing written
    assert (await reload(sessions, Turn, turn.id)).status == "running"

    assert await call(
        sessions, store.finish_turn, turn.id, turn.owner_token, status="succeeded", result="done",
        model="m1", usage={"input_tokens": 3, "output_tokens": 5}, event_type="turn.completed",
        event_data={"result": "done"},
    )
    finished = await reload(sessions, Turn, turn.id)
    assert (finished.status, finished.result, finished.model, finished.usage) == (
        "succeeded", "done", "m1", {"input_tokens": 3, "output_tokens": 5}
    )
    assert finished.finished_at is not None
    # A finished turn cannot be heartbeated or finished again.
    assert not await call(sessions, store.heartbeat, turn.id, turn.owner_token)
    assert not await call(
        sessions, store.finish_turn, turn.id, turn.owner_token, status="failed", event_type="turn.failed"
    )
    assert [e.type for e in await events_of(sessions, thread.id)] == ["turn.started", "turn.completed"]


# --- events: order ---------------------------------------------------------------


async def test_many_writers_get_unique_gap_free_seq_in_commit_order(sessions, owner):
    thread = await new_thread(sessions, owner)

    async def writer(n):
        for i in range(5):
            await call(sessions, store.append_event, thread.id, "note", {"writer": n, "i": i})

    await asyncio.gather(*(writer(n) for n in range(8)))

    events = await events_of(sessions, thread.id)
    assert [e.seq for e in events] == list(range(1, 41))
    # Commit order is seq order: rows inserted later never carry an earlier seq.
    assert [e.seq for e in sorted(events, key=lambda e: e.id)] == list(range(1, 41))
    assert (await reload(sessions, Thread, thread.id)).last_seq == 40
    # Each writer's own events kept their order.
    for n in range(8):
        assert [e.data["i"] for e in events if e.data["writer"] == n] == [0, 1, 2, 3, 4]


async def test_events_are_read_after_a_cursor_in_ascending_order(sessions, owner):
    thread = await new_thread(sessions, owner)
    for i in range(5):
        await call(sessions, store.append_event, thread.id, "note", {"i": i})
    assert [e.seq for e in await events_of(sessions, thread.id, after=2)] == [3, 4, 5]
    assert await events_of(sessions, thread.id, after=5) == []
    assert [e.seq for e in await call(sessions, store.list_events, thread.id, after=0, limit=2)] == [1, 2]
    with pytest.raises(store.ThreadNotFound):
        await call(sessions, store.append_event, 987654321, "note", {})


async def test_the_run_and_the_approval_endpoint_write_to_one_thread_in_order(sessions, owner):
    thread, turn, approval = await running_turn_with_approval(sessions, owner)

    async def run_events():
        for i in range(10):
            await call(sessions, store.append_event, thread.id, "assistant.message", {"text": str(i)}, turn_id=turn.id)

    await asyncio.gather(run_events(), call(sessions, store.decide_approval, approval.id, "allowed"))

    events = await events_of(sessions, thread.id)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert [e.seq for e in sorted(events, key=lambda e: e.id)] == [e.seq for e in events]
    assert sum(e.type == "approval.resolved" for e in events) == 1


# --- approvals -------------------------------------------------------------------


async def test_an_approval_is_recorded_with_its_event(sessions, owner):
    thread, turn, approval = await running_turn_with_approval(sessions, owner)
    got = await reload(sessions, Approval, approval.id)
    assert (got.status, got.tool, got.kind, got.input, got.delivered_at) == (
        "pending", "Bash", "command", {"command": "ls"}, None
    )
    events = await events_of(sessions, thread.id)
    assert [e.type for e in events] == ["turn.started", "approval.requested"]
    assert events[1].data["approval_id"] == approval.id and events[1].data["input"] == {"command": "ls"}

    await call(sessions, store.finish_turn, turn.id, turn.owner_token, status="succeeded")
    with pytest.raises(store.TurnNotRunning):  # no question for a turn that is not running
        await call(
            sessions, store.create_approval,
            thread_id=thread.id, turn_id=turn.id, request_id="r2", tool="Bash", input={},
        )


async def test_a_decision_wins_once_and_only_while_the_turn_runs(sessions, owner):
    thread, turn, approval = await running_turn_with_approval(sessions, owner)
    assert await call(sessions, store.decide_approval, approval.id, "allowed")
    assert not await call(sessions, store.decide_approval, approval.id, "denied")  # already decided
    assert not await call(sessions, store.expire_approval, approval.id)
    got = await reload(sessions, Approval, approval.id)
    assert (got.status, got.delivered_at) == ("allowed", None) and got.decided_at is not None
    resolved = [e for e in await events_of(sessions, thread.id) if e.type == "approval.resolved"]
    assert [e.data["status"] for e in resolved] == ["allowed"]


async def test_a_late_allow_after_the_turn_stopped_authorizes_nothing(sessions, owner):
    for how in ("interrupted", "finished"):
        thread, turn, approval = await running_turn_with_approval(sessions, owner)
        if how == "interrupted":
            await make_stale(sessions, turn.id)
            assert await call(sessions, store.interrupt_turn, turn.id, stale_before=store.utcnow() - timedelta(seconds=60))
        else:
            await call(sessions, store.finish_turn, turn.id, turn.owner_token, status="cancelled")
        # Cancelled when the turn stopped, so a late click changes no row.
        assert (await reload(sessions, Approval, approval.id)).status == "cancelled"
        assert not await call(sessions, store.decide_approval, approval.id, "allowed")
        assert not await call(sessions, store.mark_allow_delivered, approval.id, turn.owner_token)
        assert (await reload(sessions, Approval, approval.id)).status == "cancelled"


async def test_a_decision_is_refused_when_the_turn_is_not_running_even_if_the_approval_is_still_pending(
    sessions, owner
):
    """The statement checks the turn itself, not only the approval. No normal path leaves
    a pending approval on a stopped turn (stopping cancels them), so make that state by
    hand: a crash or a future bug must still not let an Allow through."""
    thread, turn, approval = await running_turn_with_approval(sessions, owner)
    async with sessions() as db:
        await db.execute(update(Turn).where(Turn.id == turn.id).values(status="interrupted"))
        await db.commit()
    assert (await reload(sessions, Approval, approval.id)).status == "pending"
    assert not await call(sessions, store.decide_approval, approval.id, "allowed")
    assert (await reload(sessions, Approval, approval.id)).status == "pending"


async def test_simultaneous_answers_produce_exactly_one_winner(sessions, owner):
    for _ in range(5):
        thread, turn, approval = await running_turn_with_approval(sessions, owner)
        results = await asyncio.gather(
            call(sessions, store.decide_approval, approval.id, "allowed"),
            call(sessions, store.decide_approval, approval.id, "denied"),
            call(sessions, store.expire_approval, approval.id),
            call(sessions, store.decide_approval, approval.id, "allowed"),
        )
        assert sum(results) == 1, results
        final = (await reload(sessions, Approval, approval.id)).status
        assert final in ("allowed", "denied", "expired")
        resolved = [e for e in await events_of(sessions, thread.id) if e.type == "approval.resolved"]
        assert [e.data["status"] for e in resolved] == [final]


async def test_expiry_authorizes_nothing_and_needs_only_a_pending_approval(sessions, owner):
    thread, turn, approval = await running_turn_with_approval(sessions, owner)
    assert await call(sessions, store.expire_approval, approval.id)
    assert not await call(sessions, store.decide_approval, approval.id, "allowed")
    assert (await reload(sessions, Approval, approval.id)).status == "expired"


async def test_an_allow_is_delivered_only_by_the_owner_and_only_once(sessions, owner):
    thread, turn, approval = await running_turn_with_approval(sessions, owner)
    # Pending (not yet decided): nothing to deliver.
    assert not await call(sessions, store.mark_allow_delivered, approval.id, turn.owner_token)
    await call(sessions, store.decide_approval, approval.id, "allowed")

    assert not await call(sessions, store.mark_allow_delivered, approval.id, "not-the-owner")
    assert (await reload(sessions, Approval, approval.id)).delivered_at is None
    assert await call(sessions, store.mark_allow_delivered, approval.id, turn.owner_token)
    assert not await call(sessions, store.mark_allow_delivered, approval.id, turn.owner_token)  # once only
    assert (await reload(sessions, Approval, approval.id)).delivered_at is not None


async def test_a_denied_approval_is_never_delivered_as_an_allow(sessions, owner):
    thread, turn, approval = await running_turn_with_approval(sessions, owner, decision="denied")
    assert not await call(sessions, store.mark_allow_delivered, approval.id, turn.owner_token)


async def test_an_allow_and_the_sweep_racing_end_delivered_or_cancelled_never_both(sessions, owner):
    outcomes = set()
    for _ in range(12):
        thread, turn, approval = await running_turn_with_approval(sessions, owner, decision="allowed")
        await make_stale(sessions, turn.id)
        cutoff = store.utcnow() - timedelta(seconds=60)
        delivered, interrupted = await asyncio.gather(
            call(sessions, store.mark_allow_delivered, approval.id, turn.owner_token),
            call(sessions, store.interrupt_turn, turn.id, stale_before=cutoff),
        )
        got = await reload(sessions, Approval, approval.id)
        if delivered:
            # The Allow was delivered first; the sweep may still end the turn but
            # cannot take the delivered Allow back.
            assert (got.status, got.delivered_at is not None) == ("allowed", True)
            outcomes.add("delivered")
        else:
            assert interrupted, "the sweep must have won"
            assert (got.status, got.delivered_at) == ("cancelled", None)
            outcomes.add("cancelled")
    assert outcomes  # either order is acceptable; mixing within one run never is


# --- finishing and interrupting -----------------------------------------------------


async def test_finishing_a_turn_cancels_its_open_approvals_in_one_transaction(sessions, owner):
    thread, turn, pending = await running_turn_with_approval(sessions, owner)
    allowed = await call(
        sessions, store.create_approval,
        thread_id=thread.id, turn_id=turn.id, request_id="r2", tool="Write", input={"path": "a"}, kind="file_write",
    )
    await call(sessions, store.decide_approval, allowed.id, "allowed")
    delivered = await call(
        sessions, store.create_approval,
        thread_id=thread.id, turn_id=turn.id, request_id="r3", tool="Bash", input={"command": "pwd"},
    )
    await call(sessions, store.decide_approval, delivered.id, "allowed")
    assert await call(sessions, store.mark_allow_delivered, delivered.id, turn.owner_token)

    assert await call(
        sessions, store.finish_turn, turn.id, turn.owner_token, status="failed", error="boom",
        error_code="runner_error", event_type="turn.failed", event_data={"error": "boom"},
    )
    statuses = {a.id: a.status for a in await call(sessions, store.list_approvals, thread.id)}
    assert statuses == {pending.id: "cancelled", allowed.id: "cancelled", delivered.id: "allowed"}
    types = [e.type for e in await events_of(sessions, thread.id)]
    assert types[-1] == "turn.failed"  # the terminal event comes last
    cancelled = [
        e.data["approval_id"] for e in await events_of(sessions, thread.id)
        if e.type == "approval.resolved" and e.data["status"] == "cancelled"
    ]
    assert sorted(cancelled) == sorted([pending.id, allowed.id])


async def test_only_a_stale_turn_is_interrupted_and_nothing_is_replayed(sessions, owner):
    live_thread = await new_thread(sessions, owner)
    live = await call(sessions, store.start_turn, live_thread.id, "still working")
    stale_thread, stale, approval = await running_turn_with_approval(sessions, owner)
    await make_stale(sessions, stale.id)

    interrupted = await call(sessions, store.interrupt_stale_turns, stale_after=timedelta(seconds=60))
    assert stale.id in interrupted and live.id not in interrupted

    got = await reload(sessions, Turn, stale.id)
    assert (got.status, got.error_code) == ("interrupted", "interrupted") and got.finished_at is not None
    assert (await reload(sessions, Turn, live.id)).status == "running"
    assert (await reload(sessions, Approval, approval.id)).status == "cancelled"
    types = [e.type for e in await events_of(sessions, stale_thread.id)]
    assert types == ["turn.started", "approval.requested", "approval.resolved", "turn.interrupted"]

    # The old run can no longer do anything with the turn.
    assert not await call(sessions, store.heartbeat, stale.id, stale.owner_token)
    assert not await call(sessions, store.finish_turn, stale.id, stale.owner_token, status="succeeded")
    # A second sweep finds nothing to do, and the thread is usable again.
    assert stale.id not in await call(sessions, store.interrupt_stale_turns, stale_after=timedelta(seconds=60))
    nxt = await call(sessions, store.start_turn, stale_thread.id, "try again")
    assert nxt.seq == 2


async def test_two_sweeps_racing_on_one_stale_turn_interrupt_it_once(sessions, owner):
    thread, turn, _ = await running_turn_with_approval(sessions, owner)
    await make_stale(sessions, turn.id)
    cutoff = store.utcnow() - timedelta(seconds=60)
    results = await asyncio.gather(
        *(call(sessions, store.interrupt_turn, turn.id, stale_before=cutoff) for _ in range(4))
    )
    assert sum(results) == 1
    types = [e.type for e in await events_of(sessions, thread.id)]
    assert types.count("turn.interrupted") == 1 and types.count("approval.resolved") == 1


async def test_a_run_that_beats_the_sweep_to_it_is_not_interrupted(sessions, owner):
    """The sweep picked the turn as stale, then the run refreshed its lease before the
    sweep's own update ran: the update must re-check, and lose."""
    thread = await new_thread(sessions, owner)
    turn = await call(sessions, store.start_turn, thread.id, "slow but alive")
    await make_stale(sessions, turn.id)
    cutoff = store.utcnow() - timedelta(seconds=60)
    assert await call(sessions, store.heartbeat, turn.id, turn.owner_token)
    assert not await call(sessions, store.interrupt_turn, turn.id, stale_before=cutoff)
    assert (await reload(sessions, Turn, turn.id)).status == "running"
    assert [e.type for e in await events_of(sessions, thread.id)] == ["turn.started"]


async def test_a_turn_that_keeps_its_heartbeat_is_left_alone(sessions, owner):
    thread = await new_thread(sessions, owner)
    turn = await call(sessions, store.start_turn, thread.id, "long job")
    await make_stale(sessions, turn.id)
    assert await call(sessions, store.heartbeat, turn.id, turn.owner_token)  # the run is alive
    assert turn.id not in await call(sessions, store.interrupt_stale_turns, stale_after=timedelta(seconds=60))
    assert (await reload(sessions, Turn, turn.id)).status == "running"


# --- history for a provider with no session of its own --------------------------------


async def test_history_is_rebuilt_in_order_with_tool_calls_joined(sessions, owner):
    thread = await new_thread(sessions, owner, provider="local-openai", session_state="none", provider_session_id=None)
    turn = await call(sessions, store.start_turn, thread.id, "list the files")
    add = lambda t, d: call(sessions, store.append_event, thread.id, t, d, turn_id=turn.id)  # noqa: E731
    await add("assistant.message", {"text": "Let me look."})
    await add("tool.started", {"call_id": "c1", "name": "Bash", "input": {"command": "ls"}})
    await add("tool.completed", {"call_id": "c1", "ok": True, "output": "a.txt\nb.txt", "truncated": False})
    await add("tool.started", {"call_id": "c2", "name": "Read", "input": {"path": "x"}})  # never completed
    await add("approval.resolved", {"approval_id": "a", "status": "allowed"})  # not part of the history
    await add("assistant.message", {"text": "Two files, and then I was cut o", "incomplete": True})

    history = store.history_items(await events_of(sessions, thread.id))
    assert [(h["role"], h.get("text") or h.get("name")) for h in history] == [
        ("user", "list the files"), ("assistant", "Let me look."), ("tool", "Bash"), ("tool", "Read"),
        ("assistant", "Two files, and then I was cut o"),
    ]
    bash, read = history[2], history[3]
    assert (bash["ok"], bash["output"], bash["input"]) == (True, "a.txt\nb.txt", {"command": "ls"})
    assert (read["ok"], read["output"]) == (None, None)  # interrupted: no result, not invented
    assert [h.get("incomplete") for h in history if h["role"] == "assistant"] == [False, True]


def test_tool_output_is_capped_and_says_so():
    short, cut = store.cap_tool_output("hello")
    assert (short, cut) == ("hello", False)
    long_text = "x" * (store.TOOL_OUTPUT_CAP + 10)
    capped, cut = store.cap_tool_output(long_text)
    assert cut and len(capped) == store.TOOL_OUTPUT_CAP


def test_titles_are_one_clipped_line():
    assert store.make_title("  hello \n world ") == "hello world"
    assert store.make_title("") == ""
    title = store.make_title("word " * 40)
    assert len(title) == store.TITLE_MAX_CHARS and title.endswith("…")


# --- slice 2 additions: owner-checked events, cancel, finish extras, own interrupt ------


async def test_a_run_writes_events_only_while_it_owns_the_turn(sessions, owner):
    thread = await new_thread(sessions, owner)
    turn = await call(sessions, store.start_turn, thread.id, "work")
    await make_stale(sessions, turn.id)

    event = await call(
        sessions, store.append_turn_event, turn.id, turn.owner_token, "assistant.message", {"text": "hi"}
    )
    assert (event.seq, event.turn_id, event.data) == (2, turn.id, {"text": "hi"})
    # Writing an event is also a heartbeat: the lease was renewed.
    assert (await reload(sessions, Turn, turn.id)).heartbeat_at > store.utcnow() - timedelta(seconds=30)

    assert await call(sessions, store.append_turn_event, turn.id, "not-the-owner", "x", {}) is None
    assert [e.type for e in await events_of(sessions, thread.id)] == ["turn.started", "assistant.message"]
    await call(sessions, store.finish_turn, turn.id, turn.owner_token, status="succeeded")
    assert await call(sessions, store.append_turn_event, turn.id, turn.owner_token, "x", {}) is None


async def test_a_withdrawn_question_is_cancelled_once_and_never_after_a_decision(sessions, owner):
    thread, turn, approval = await running_turn_with_approval(sessions, owner)
    assert await call(sessions, store.cancel_approval, approval.id)
    assert not await call(sessions, store.cancel_approval, approval.id)
    assert not await call(sessions, store.decide_approval, approval.id, "allowed")
    resolved = [e for e in await events_of(sessions, thread.id) if e.type == "approval.resolved"]
    assert [e.data["status"] for e in resolved] == ["cancelled"]

    _, _, decided = await running_turn_with_approval(sessions, owner, decision="allowed")
    assert not await call(sessions, store.cancel_approval, decided.id)  # a decision stands


async def test_finishing_stores_extra_events_before_the_terminal_one_and_sets_the_session_state(
    sessions, owner
):
    thread = await new_thread(sessions, owner, session_state="new")
    turn = await call(sessions, store.start_turn, thread.id, "work")
    assert not await call(
        sessions, store.finish_turn, turn.id, "not-the-owner", status="failed",
        extra_events=[("assistant.message", {"text": "x"})], session_state="active",
        event_type="turn.failed",
    )
    assert (await reload(sessions, Thread, thread.id)).session_state == "new"  # nothing leaked through

    assert await call(
        sessions, store.finish_turn, turn.id, turn.owner_token, status="failed", error="boom",
        extra_events=[("assistant.message", {"text": "partial", "incomplete": True})],
        session_state="active", event_type="turn.failed", event_data={"error": "boom"},
    )
    assert [e.type for e in await events_of(sessions, thread.id)] == [
        "turn.started", "assistant.message", "turn.failed"
    ]
    assert (await reload(sessions, Thread, thread.id)).session_state == "active"
    with pytest.raises(ValueError):
        await call(
            sessions, store.finish_turn, turn.id, turn.owner_token, status="failed", session_state="bogus"
        )


async def test_a_process_can_interrupt_only_its_own_turn_when_it_shuts_down(sessions, owner):
    thread, turn, approval = await running_turn_with_approval(sessions, owner)
    assert not await call(sessions, store.interrupt_own_turn, turn.id, "someone-elses-token")
    assert (await reload(sessions, Turn, turn.id)).status == "running"
    assert await call(sessions, store.interrupt_own_turn, turn.id, turn.owner_token, reason="shutdown")
    assert not await call(sessions, store.interrupt_own_turn, turn.id, turn.owner_token)  # once
    events = await events_of(sessions, thread.id)
    assert events[-1].type == "turn.interrupted" and events[-1].data == {"reason": "shutdown", "replay": "after_accept"}
    assert (await reload(sessions, Approval, approval.id)).status == "cancelled"


async def test_a_session_reset_gives_a_new_session_keeps_the_transcript_and_records_it(sessions, owner):
    thread = await new_thread(sessions, owner, session_state="broken")
    turn = await call(sessions, store.start_turn, thread.id, "before")
    await call(sessions, store.finish_turn, turn.id, turn.owner_token, status="failed", event_type="turn.failed")
    old_session = thread.provider_session_id

    reset = await call(sessions, store.reset_session, thread.id, "brand-new-session", message="started over")
    assert (reset.provider_session_id, reset.session_state) == ("brand-new-session", "new")
    assert reset.updated_at is not None  # readable after the session closed
    stored = await reload(sessions, Thread, thread.id)
    assert (stored.provider_session_id, stored.session_state) != (old_session, "broken")
    events = await events_of(sessions, thread.id)
    assert [e.type for e in events] == ["turn.started", "turn.failed", "session.reset"]  # the transcript stays
    assert events[-1].turn_id is None
    assert events[-1].data == {"message": "started over", "previous_state": "broken"}

    # A provider that keeps no session ends up in the `none` state.
    none = await call(sessions, store.reset_session, thread.id, None, message="no session")
    assert (none.provider_session_id, none.session_state) == (None, "none")


async def test_a_session_cannot_be_reset_while_a_turn_runs_or_for_a_missing_thread(sessions, owner):
    thread = await new_thread(sessions, owner)
    await call(sessions, store.start_turn, thread.id, "running")
    with pytest.raises(store.ThreadBusy):
        await call(sessions, store.reset_session, thread.id, "x", message="m")
    assert (await reload(sessions, Thread, thread.id)).provider_session_id == thread.provider_session_id
    assert [e.type for e in await events_of(sessions, thread.id)] == ["turn.started"]  # nothing written
    with pytest.raises(store.ThreadNotFound):
        await call(sessions, store.reset_session, 987654321, "x", message="m")


async def test_two_threads_cannot_end_up_sharing_a_session_through_a_reset(sessions, owner):
    first = await new_thread(sessions, owner, provider_session_id="taken")
    second = await new_thread(sessions, owner)
    with pytest.raises(IntegrityError):
        await call(sessions, store.reset_session, second.id, "taken", message="m")
    assert first.id != second.id


async def test_all_events_are_read_across_pages(sessions, owner):
    thread = await new_thread(sessions, owner)
    for i in range(7):
        await call(sessions, store.append_event, thread.id, "note", {"i": i})
    assert [e.seq for e in await call(sessions, store.list_all_events, thread.id, page=3)] == list(range(1, 8))
    assert [e.seq for e in await call(sessions, store.list_all_events, thread.id, page=7)] == list(range(1, 8))
    assert await call(sessions, store.get_approval, "missing") is None


# --- managing conversations: rename, archive, delete ----------------------------------------


async def titles(sessions, owner, **kwargs):
    return [t.title for t in await call(sessions, store.list_threads, owner, **kwargs)]


async def test_a_rename_sets_the_title_and_is_not_activity(sessions, owner):
    older = await new_thread(sessions, owner, title="older")
    newer = await new_thread(sessions, owner, title="newer")
    before = (await call(sessions, store.get_thread, older.id)).updated_at
    renamed = await call(sessions, store.rename_thread, older.id, "renamed")
    assert renamed.title == "renamed"
    assert renamed.updated_at == before
    assert await titles(sessions, owner) == ["newer", "renamed"]  # the order did not change
    assert newer.id != older.id


async def test_archiving_hides_a_thread_from_the_default_list_and_unarchiving_brings_it_back(sessions, owner):
    keep = await new_thread(sessions, owner, title="keep")
    hide = await new_thread(sessions, owner, title="hide")
    archived = await call(sessions, store.set_archived, hide.id, True)
    assert archived.archived_at is not None
    assert await titles(sessions, owner) == ["keep"]
    assert sorted(await titles(sessions, owner, include_archived=True)) == ["hide", "keep"]
    restored = await call(sessions, store.set_archived, hide.id, False)
    assert restored.archived_at is None
    assert sorted(await titles(sessions, owner)) == ["hide", "keep"]
    assert keep.id != hide.id


async def test_archiving_a_thread_with_a_running_turn_is_refused_and_changes_nothing(sessions, owner):
    thread = await new_thread(sessions, owner)
    turn = await call(sessions, store.start_turn, thread.id, "work")
    with pytest.raises(store.ThreadBusy):
        await call(sessions, store.set_archived, thread.id, True)
    assert (await call(sessions, store.get_thread, thread.id)).archived_at is None
    assert await call(sessions, store.finish_turn, turn.id, turn.owner_token, status="succeeded")
    assert (await call(sessions, store.set_archived, thread.id, True)).archived_at is not None


async def test_delete_removes_the_thread_its_turns_events_and_approvals_and_nothing_else(sessions, owner):
    doomed, other, approval = None, None, None
    doomed, turn, approval = await running_turn_with_approval(sessions, owner)
    assert await call(sessions, store.finish_turn, turn.id, turn.owner_token, status="succeeded", event_type="turn.completed")
    other, other_turn, other_approval = await running_turn_with_approval(sessions, owner)
    assert await call(sessions, store.finish_turn, other_turn.id, other_turn.owner_token, status="succeeded")

    await call(sessions, store.delete_thread, doomed.id)

    assert await call(sessions, store.get_thread, doomed.id) is None
    assert await call(sessions, store.list_turns, doomed.id) == []
    assert await events_of(sessions, doomed.id) == []
    assert await call(sessions, store.get_approval, approval.id) is None
    # the other conversation is untouched
    assert (await call(sessions, store.get_thread, other.id)).id == other.id
    assert len(await call(sessions, store.list_turns, other.id)) == 1
    assert await events_of(sessions, other.id)
    assert await call(sessions, store.get_approval, other_approval.id) is not None


async def test_a_deleted_conversations_ids_are_never_given_to_a_new_one(sessions, owner):
    """Links (/chat?thread=N) and the decision log name a conversation by id, so
    deleting the newest conversation must not hand its id, or the ids of its
    turns and events, to the next one (SQLite reused them before AUTOINCREMENT)."""
    doomed, turn, _approval = await running_turn_with_approval(sessions, owner)
    assert await call(sessions, store.finish_turn, turn.id, turn.owner_token, status="succeeded", event_type="turn.completed")
    old_events = [e.id for e in await events_of(sessions, doomed.id)]

    await call(sessions, store.delete_thread, doomed.id)

    fresh = await new_thread(sessions, owner)
    fresh_turn = await call(sessions, store.start_turn, fresh.id, "again")
    assert fresh.id > doomed.id
    assert fresh_turn.id > turn.id
    assert min(e.id for e in await events_of(sessions, fresh.id)) > max(old_events)


async def test_delete_while_a_turn_runs_is_refused_and_deletes_nothing(sessions, owner):
    thread, turn, approval = await running_turn_with_approval(sessions, owner)
    with pytest.raises(store.ThreadBusy):
        await call(sessions, store.delete_thread, thread.id)
    assert await call(sessions, store.get_thread, thread.id) is not None
    assert await call(sessions, store.get_approval, approval.id) is not None
    assert await events_of(sessions, thread.id)
    assert turn.status == "running"


async def test_managing_a_thread_that_does_not_exist_says_so(sessions):
    with pytest.raises(store.ThreadNotFound):
        await call(sessions, store.rename_thread, 987654321, "x")
    with pytest.raises(store.ThreadNotFound):
        await call(sessions, store.set_archived, 987654321, True)
    with pytest.raises(store.ThreadNotFound):
        await call(sessions, store.delete_thread, 987654321)


async def test_a_new_turn_cannot_start_in_a_thread_that_is_being_deleted(sessions, owner):
    thread = await new_thread(sessions, owner)
    await call(sessions, store.delete_thread, thread.id)
    with pytest.raises(store.ThreadNotFound):
        await call(sessions, store.start_turn, thread.id, "too late")
