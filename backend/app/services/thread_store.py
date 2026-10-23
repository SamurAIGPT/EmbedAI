"""Persistence for durable threads, turns, events and approvals (F04, slice 1).

This is the data layer only. It knows nothing about Claude or any other
provider, and it starts no processes. The recorder, adapters and endpoints
(slices 2 and 3) are built on top of it.

Every public function is one transaction: it takes the write lock first
(`begin_write`, so on SQLite writers queue instead of failing on a stale
snapshot), does its statements, and commits. When it returns, what it wrote is
in the database, which is what lets a caller broadcast afterwards ("store
before broadcast"). Every state change that must not race is one conditional
UPDATE; the caller learns whether it won from the return value.

Event payloads, so adapters and readers agree:

    turn.started        {"prompt_text", "attachments"?: [{"id", "name", "mime", "bytes", "kind"}]}
    assistant.message   {"text", "incomplete"?}
    tool.started        {"call_id", "name", "input"}
    tool.completed      {"call_id", "ok", "output", "truncated"}
    approval.requested  {"approval_id", "tool", "kind", "summary", "input"}
    approval.resolved   {"approval_id", "status"}
    turn.completed / turn.failed / turn.cancelled / turn.interrupted
    session.reset

`type` is free text: a new type needs no migration.
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Approval, Event, Notification, Thread, Turn
from app.db.models.thread import SESSION_STATES
from app.db.transactions import begin_write

TITLE_MAX_CHARS = 60
# A tool result longer than this is cut, and the event says so.
TOOL_OUTPUT_CAP = 100_000
# Statuses a run may finish a turn with. `interrupted` is only ever set by the
# sweep, never by the run itself.
FINISH_STATUSES = ("succeeded", "failed", "cancelled")
DECISIONS = ("allowed", "denied")


class ThreadNotFound(Exception):
    pass


class ThreadBusy(Exception):
    """A turn is already running in this thread."""


class TurnNotRunning(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_title(prompt: str) -> str:
    text = " ".join(prompt.split())
    if len(text) <= TITLE_MAX_CHARS:
        return text
    return text[: TITLE_MAX_CHARS - 1].rstrip() + "…"


def cap_tool_output(output: str) -> tuple[str, bool]:
    """The output to store and whether it was cut."""
    if len(output) <= TOOL_OUTPUT_CAP:
        return output, False
    return output[:TOOL_OUTPUT_CAP], True


def _check(value: str, allowed: tuple[str, ...], what: str) -> None:
    if value not in allowed:
        raise ValueError(f"{what} must be one of {', '.join(allowed)} (got {value!r})")


# --- reads ---------------------------------------------------------------------


async def get_thread(db: AsyncSession, thread_id: int) -> Thread | None:
    return await db.get(Thread, thread_id)


async def list_threads(
    db: AsyncSession, owner_id: str, *, include_archived: bool = False, limit: int = 100, kind: str = "chat"
) -> list[Thread]:
    query = select(Thread).where(Thread.owner_id == owner_id)
    if kind == "chat":
        # The lead's report threads ("Team incidents") sit with the person's chats.
        query = query.where(Thread.kind.in_(("chat", "team_notices")))
    elif kind != "all":
        query = query.where(Thread.kind == kind)
    if not include_archived:
        query = query.where(Thread.archived_at.is_(None))
    query = query.order_by(Thread.updated_at.desc(), Thread.id.desc()).limit(limit)
    return list((await db.execute(query)).scalars())


async def _locked_thread(db: AsyncSession, thread_id: int) -> Thread:
    """Start a write transaction and lock the thread's row, as `start_turn` does,
    so a check of its turns and the change that follows cannot interleave with a
    new turn. Raises ThreadNotFound (and ends the transaction)."""
    await begin_write(db)
    thread = (
        await db.execute(select(Thread).where(Thread.id == thread_id).with_for_update())
    ).scalar_one_or_none()
    if thread is None:
        await db.rollback()
        raise ThreadNotFound(thread_id)
    return thread


async def running_turn_id(db: AsyncSession, thread_id: int) -> int | None:
    """The id of the thread's running turn, if there is one."""
    running = await db.execute(
        select(Turn.id).where(Turn.thread_id == thread_id, Turn.status == "running").limit(1)
    )
    return running.scalar_one_or_none()


async def _has_running_turn(db: AsyncSession, thread_id: int) -> bool:
    return await running_turn_id(db, thread_id) is not None


async def _change_quietly(db: AsyncSession, thread_id: int, **values) -> Thread:
    """Change columns without counting it as activity. The model refreshes
    `updated_at` on every ORM update, which would move a renamed or archived
    thread in the list, so the statement keeps the column as it is."""
    await db.execute(
        update(Thread)
        .where(Thread.id == thread_id)
        .values(**values, updated_at=Thread.updated_at)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return await db.get(Thread, thread_id, populate_existing=True)


async def rename_thread(db: AsyncSession, thread_id: int, title: str) -> Thread:
    """Set the title. A rename is not activity: the list order does not change.
    Raises ThreadNotFound."""
    await _locked_thread(db, thread_id)
    return await _change_quietly(db, thread_id, title=title)


async def set_model(db: AsyncSession, thread_id: int, model: str | None) -> Thread:
    """Change the model the next turns ask for (None: the engine's default).
    Not activity: the list order does not change. Raises ThreadNotFound."""
    await _locked_thread(db, thread_id)
    return await _change_quietly(db, thread_id, model=model)


async def set_archived(db: AsyncSession, thread_id: int, archived: bool) -> Thread:
    """Hide a thread from the default list (or bring it back). Nothing is
    deleted. Raises ThreadNotFound, or ThreadBusy when archiving a thread whose
    turn is running."""
    await _locked_thread(db, thread_id)
    if archived and await _has_running_turn(db, thread_id):
        await db.rollback()
        raise ThreadBusy(thread_id)
    return await _change_quietly(db, thread_id, archived_at=utcnow() if archived else None)


async def delete_thread(db: AsyncSession, thread_id: int) -> None:
    """Remove a thread with its turns, events and approvals, in one transaction.
    Raises ThreadNotFound, or ThreadBusy while a turn is running (stop it
    first): the run is still writing to it."""
    await _locked_thread(db, thread_id)
    if await _has_running_turn(db, thread_id):
        await db.rollback()
        raise ThreadBusy(thread_id)
    # Children first: events and approvals point at turns, all of them at the thread.
    # Notifications remain in the feed, but no longer have a conversation to open.
    await db.execute(
        update(Notification)
        .where(Notification.thread_id == thread_id)
        .values(thread_id=None)
    )
    await db.execute(delete(Approval).where(Approval.thread_id == thread_id))
    await db.execute(delete(Event).where(Event.thread_id == thread_id))
    await db.execute(delete(Turn).where(Turn.thread_id == thread_id))
    await db.execute(delete(Thread).where(Thread.id == thread_id))
    await db.commit()


async def get_turn(db: AsyncSession, turn_id: int) -> Turn | None:
    return await db.get(Turn, turn_id)


async def list_turns(db: AsyncSession, thread_id: int) -> list[Turn]:
    query = select(Turn).where(Turn.thread_id == thread_id).order_by(Turn.seq)
    return list((await db.execute(query)).scalars())


async def list_events(
    db: AsyncSession, thread_id: int, *, after: int = 0, limit: int = 500
) -> list[Event]:
    """Stored events with seq > after, ascending. This is what a stream reads."""
    query = (
        select(Event)
        .where(Event.thread_id == thread_id, Event.seq > after)
        .order_by(Event.seq)
        .limit(limit)
    )
    return list((await db.execute(query)).scalars())


async def get_approval(db: AsyncSession, approval_id: str) -> Approval | None:
    return await db.get(Approval, approval_id)


async def list_all_events(db: AsyncSession, thread_id: int, *, page: int = 500) -> list[Event]:
    """Every stored event of a thread, ascending (read in pages)."""
    events: list[Event] = []
    after = 0
    while True:
        batch = await list_events(db, thread_id, after=after, limit=page)
        events.extend(batch)
        if len(batch) < page:
            return events
        after = batch[-1].seq


async def list_approvals(
    db: AsyncSession, thread_id: int, *, status: str | None = None
) -> list[Approval]:
    query = select(Approval).where(Approval.thread_id == thread_id)
    if status is not None:
        query = query.where(Approval.status == status)
    return list((await db.execute(query.order_by(Approval.created_at, Approval.id))).scalars())


def history_items(events: list[Event]) -> list[dict]:
    """A thread's stored events as an ordered history: the prompt, each
    assistant message, and each tool call with its result joined by call_id.

    A usable history for a provider that keeps no session of its own, not a
    lossless record: tool output is capped, and a call with no completion (the
    turn was interrupted) has output None."""
    items: list[dict] = []
    calls: dict[str, dict] = {}
    for event in sorted(events, key=lambda e: e.seq):
        data = event.data or {}
        if event.type == "turn.started":
            items.append({"role": "user", "text": data.get("prompt_text", ""), "turn_id": event.turn_id})
        elif event.type == "assistant.message":
            items.append(
                {
                    "role": "assistant",
                    "text": data.get("text", ""),
                    "incomplete": bool(data.get("incomplete")),
                    "turn_id": event.turn_id,
                }
            )
        elif event.type == "tool.started":
            item = {
                "role": "tool",
                "call_id": data.get("call_id"),
                "name": data.get("name"),
                "input": data.get("input"),
                "output": None,
                "ok": None,
                "truncated": False,
                "turn_id": event.turn_id,
            }
            items.append(item)
            if item["call_id"] is not None:
                calls[item["call_id"]] = item
        elif event.type == "tool.completed":
            item = calls.get(data.get("call_id"))
            if item is None:  # a completion whose start was never stored
                item = {
                    "role": "tool",
                    "call_id": data.get("call_id"),
                    "name": None,
                    "input": None,
                    "turn_id": event.turn_id,
                }
                items.append(item)
            item["output"] = data.get("output")
            item["ok"] = data.get("ok")
            item["truncated"] = bool(data.get("truncated"))
        elif event.type == "context.compacted":
            # Earlier messages folded into a summary (context_compaction.py).
            # Adapters ignore this role; the replay planner applies it.
            items.append(
                {
                    "role": "compaction",
                    "summary": data.get("summary"),
                    "folded_through_turn_id": data.get("folded_through_turn_id"),
                    "turn_id": event.turn_id,
                }
            )
    return items


# --- threads and events ----------------------------------------------------------


async def _next_seq(db: AsyncSession, thread_id: int) -> int:
    """Hand out the thread's next event number. The UPDATE takes the thread
    row's write lock, which is held until the transaction commits, so two
    writers cannot interleave: commit order is seq order."""
    result = await db.execute(
        update(Thread)
        .where(Thread.id == thread_id)
        # updated_at is set here, with microseconds: the database's own now() has
        # one-second resolution on SQLite, which would tie busy threads.
        .values(last_seq=Thread.last_seq + 1, updated_at=utcnow())
        .returning(Thread.last_seq)
        .execution_options(synchronize_session=False)
    )
    seq = result.scalar_one_or_none()
    if seq is None:
        raise ThreadNotFound(thread_id)
    return seq


async def _add_event(
    db: AsyncSession, thread_id: int, type_: str, data: dict | None, turn_id: int | None
) -> Event:
    event = Event(
        thread_id=thread_id,
        turn_id=turn_id,
        seq=await _next_seq(db, thread_id),
        type=type_,
        data=data or {},
    )
    db.add(event)
    await db.flush()
    return event


async def create_thread(
    db: AsyncSession,
    *,
    owner_id: str,
    provider: str,
    model: str | None = None,
    provider_session_id: str | None = None,
    session_state: str = "new",
    provider_state: dict | None = None,
    title: str | None = None,
    workspace_path: str | None = None,
    agent_id: int | None = None,
    kind: str = "chat",
    job_id: int | None = None,
    room_id: int | None = None,
) -> Thread:
    if not provider.strip():
        raise ValueError("provider is required")
    _check(session_state, SESSION_STATES, "session_state")
    if workspace_path is None:
        from app.services.local_claude_runner import workspace_dir
        workspace_path = str(workspace_dir().resolve())
    await begin_write(db)
    now = utcnow()
    thread = Thread(
        owner_id=owner_id,
        created_at=now,
        updated_at=now,
        title=title,
        provider=provider,
        model=model,
        provider_session_id=provider_session_id,
        session_state=session_state,
        provider_state=provider_state,
        workspace_path=workspace_path,
        agent_id=agent_id, kind=kind, job_id=job_id, room_id=room_id,
    )
    db.add(thread)
    await db.flush()
    await db.commit()
    return thread


async def set_session_state(
    db: AsyncSession, thread_id: int, state: str, *, expected: str | None = None
) -> bool:
    """Change the session state; with `expected`, only if the thread is still in
    that state. True if a row changed."""
    _check(state, SESSION_STATES, "session_state")
    await begin_write(db)
    query = update(Thread).where(Thread.id == thread_id)
    if expected is not None:
        query = query.where(Thread.session_state == expected)
    result = await db.execute(query.values(session_state=state).execution_options(synchronize_session=False))
    await db.commit()
    return result.rowcount == 1


async def reset_session(
    db: AsyncSession, thread_id: int, new_session_id: str | None, *, message: str
) -> Thread:
    """Give the thread a new provider session (or none, when `new_session_id` is
    None) and record a `session.reset` event, in one transaction. The stored
    transcript is untouched; only the provider's own context is gone. Refused
    (ThreadBusy) while a turn is running, because that turn is using the old
    session."""
    await begin_write(db)
    thread = (
        await db.execute(select(Thread).where(Thread.id == thread_id).with_for_update())
    ).scalar_one_or_none()
    if thread is None:
        await db.rollback()
        raise ThreadNotFound(thread_id)
    running = (
        await db.execute(select(Turn.id).where(Turn.thread_id == thread_id, Turn.status == "running"))
    ).scalar_one_or_none()
    if running is not None:
        await db.rollback()
        raise ThreadBusy(thread_id)
    previous = thread.session_state
    thread.provider_session_id = new_session_id
    thread.session_state = "new" if new_session_id is not None else "none"
    # The engine's own conversation id belongs to the old session.
    thread.provider_state = None
    # Set here, in Python: left to the database's onupdate it would be expired
    # after the commit and unreadable once the session is closed.
    thread.updated_at = utcnow()
    await _add_event(
        db, thread_id, "session.reset", {"message": message, "previous_state": previous}, None
    )
    await db.commit()
    return thread


async def append_event(
    db: AsyncSession,
    thread_id: int,
    type_: str,
    data: dict | None = None,
    *,
    turn_id: int | None = None,
) -> Event:
    """Store one event with the thread's next seq and commit."""
    await begin_write(db)
    event = await _add_event(db, thread_id, type_, data, turn_id)
    await db.commit()
    return event


async def append_turn_event(
    db: AsyncSession,
    turn_id: int,
    owner_token: str,
    type_: str,
    data: dict | None = None,
) -> Event | None:
    """Store an event for a turn the caller owns. One transaction: the same
    statement that proves the turn is still running under `owner_token` also
    refreshes its heartbeat, so every event a run writes doubles as a lease
    check. Returns None (and writes nothing) if the caller no longer owns the
    turn: the run must stop."""
    await begin_write(db)
    touched = await db.execute(
        update(Turn)
        .where(Turn.id == turn_id, Turn.status == "running", Turn.owner_token == owner_token)
        .values(heartbeat_at=utcnow())
        .returning(Turn.thread_id)
        .execution_options(synchronize_session=False)
    )
    thread_id = touched.scalar_one_or_none()
    if thread_id is None:
        await db.rollback()
        return None
    event = await _add_event(db, thread_id, type_, data, turn_id)
    await db.commit()
    return event


# --- turns -------------------------------------------------------------------


async def start_turn(
    db: AsyncSession,
    thread_id: int,
    prompt_text: str,
    *,
    owner_instance: str | None = None,
    attachments: list[dict] | None = None,
    origin: str | None = None,
) -> Turn:
    """Start the thread's next turn and store its `turn.started` event.

    The returned turn carries the `owner_token` that only the run holds. Raises
    ThreadBusy if a turn is already running (the database refuses a second one),
    ThreadNotFound if the thread does not exist."""
    await begin_write(db)
    thread = (
        await db.execute(select(Thread).where(Thread.id == thread_id).with_for_update())
    ).scalar_one_or_none()
    if thread is None:
        await db.rollback()
        raise ThreadNotFound(thread_id)
    last = (
        await db.execute(select(func.coalesce(func.max(Turn.seq), 0)).where(Turn.thread_id == thread_id))
    ).scalar_one()
    now = utcnow()
    turn = Turn(
        thread_id=thread_id,
        seq=last + 1,
        prompt_text=prompt_text,
        status="running",
        owner_token=secrets.token_urlsafe(24),
        owner_instance=owner_instance,
        engine_phase="preparing",
        origin=origin,
        heartbeat_at=now,
        started_at=now,
    )
    db.add(turn)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ThreadBusy(thread_id) from exc
    if thread.title is None:
        thread.title = make_title(prompt_text) or None
    started = {"prompt_text": prompt_text}
    if attachments:
        started["attachments"] = attachments
    await _add_event(db, thread_id, "turn.started", started, turn.id)
    await db.commit()
    return turn


async def heartbeat(db: AsyncSession, turn_id: int, owner_token: str) -> bool:
    """Refresh the lease. False means the run no longer owns the turn: it must
    stop (kill its child and write nothing more)."""
    await begin_write(db)
    result = await db.execute(
        update(Turn)
        .where(Turn.id == turn_id, Turn.status == "running", Turn.owner_token == owner_token)
        .values(heartbeat_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount == 1


async def finish_turn(
    db: AsyncSession,
    turn_id: int,
    owner_token: str,
    *,
    status: str,
    result: str | None = None,
    error: str | None = None,
    error_code: str | None = None,
    model: str | None = None,
    usage: dict | None = None,
    event_type: str | None = None,
    event_data: dict | None = None,
    extra_events: list[tuple[str, dict]] | None = None,
    session_state: str | None = None,
    provider_state: dict | None = None,
) -> bool:
    """Finish a turn the caller owns, store its terminal event and cancel any
    approval still open, all in one transaction. `extra_events` are stored
    before the terminal event (for example the partial answer of a failed
    turn), and `session_state` and `provider_state`, if given, are set on the
    thread in the same transaction. False (and nothing written) if the caller
    no longer owns the turn."""
    _check(status, FINISH_STATUSES, "status")
    if session_state is not None:
        _check(session_state, SESSION_STATES, "session_state")
    await begin_write(db)
    updated = await db.execute(
        update(Turn)
        .where(Turn.id == turn_id, Turn.status == "running", Turn.owner_token == owner_token)
        .values(
            status=status,
            result=result,
            error=error,
            error_code=error_code,
            model=model,
            usage=usage,
            finished_at=utcnow(),
        )
        .returning(Turn.thread_id)
        .execution_options(synchronize_session=False)
    )
    thread_id = updated.scalar_one_or_none()
    if thread_id is None:
        await db.rollback()
        return False
    thread_values: dict = {}
    if session_state is not None:
        thread_values["session_state"] = session_state
    if provider_state is not None:
        thread_values["provider_state"] = provider_state
    if thread_values:
        await db.execute(
            update(Thread)
            .where(Thread.id == thread_id)
            .values(**thread_values)
            .execution_options(synchronize_session=False)
        )
    await _cancel_open_approvals(db, thread_id, turn_id)
    for extra_type, extra_data in extra_events or []:
        await _add_event(db, thread_id, extra_type, extra_data, turn_id)
    if event_type:
        await _add_event(db, thread_id, event_type, event_data, turn_id)
    await db.commit()
    return True


# Events a turn stores that are not the engine's own work.
NOT_ENGINE_EVENTS = ("turn.started", "memory.recalled", "context.compacted", "engine.switched", "approval.resolved")

# How far a turn cut by a crash or a stop had got (docs/RELIABILITY.md):
# before_accept  the prompt never reached the engine: nothing ran, safe to send again;
# after_accept   the engine produced output or tool calls: it may have changed things;
# unknown        the prompt reached the engine, which had reported nothing yet;
#                treated like after_accept.
# None of them is ever re-run automatically.
BEFORE_ACCEPT, AFTER_ACCEPT, UNKNOWN_ACCEPT = "before_accept", "after_accept", "unknown"


async def mark_engine_sent(db: AsyncSession, turn_id: int, owner_token: str) -> bool:
    """The run is handing the prompt to its engine now. False means the run no
    longer owns its turn."""
    await begin_write(db)
    result = await db.execute(
        update(Turn)
        .where(Turn.id == turn_id, Turn.status == "running", Turn.owner_token == owner_token)
        .values(engine_phase="sent")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount == 1


async def _interrupt(db: AsyncSession, turn_id: int, *conditions, reason: str) -> bool:
    """Move a running turn to `interrupted` under extra conditions, in one
    atomic update, and (only for the winner) cancel its open approvals and store
    `turn.interrupted`, in the same transaction. The event says how far the
    turn had got (`replay`), from its engine phase and stored events."""
    await begin_write(db)
    updated = await db.execute(
        update(Turn)
        .where(Turn.id == turn_id, Turn.status == "running", *conditions)
        .values(status="interrupted", finished_at=utcnow(), error_code="interrupted")
        .returning(Turn.thread_id, Turn.engine_phase)
        .execution_options(synchronize_session=False)
    )
    row = updated.one_or_none()
    if row is None:
        await db.rollback()
        return False
    thread_id, phase = row
    worked = (
        await db.execute(
            select(Event.id).where(Event.turn_id == turn_id, Event.type.not_in(NOT_ENGINE_EVENTS)).limit(1)
        )
    ).first()
    if worked is not None:
        replay = AFTER_ACCEPT
    elif phase == "preparing":
        replay = BEFORE_ACCEPT
    else:  # "sent", or a turn from before phases were recorded
        replay = UNKNOWN_ACCEPT
    await _cancel_open_approvals(db, thread_id, turn_id)
    await _add_event(db, thread_id, "turn.interrupted", {"reason": reason, "replay": replay}, turn_id)
    await db.commit()
    return True


async def interrupt_turn(db: AsyncSession, turn_id: int, *, stale_before: datetime) -> bool:
    """Mark a running turn `interrupted` if its heartbeat is older than
    `stale_before`: one atomic update, so only one caller can win. Nothing is
    resumed or replayed."""
    return await _interrupt(
        db, turn_id, Turn.heartbeat_at < stale_before, reason="lease_expired"
    )


async def interrupt_own_turn(
    db: AsyncSession, turn_id: int, owner_token: str, *, reason: str = "shutdown"
) -> bool:
    """A run's own process is going away (a clean shutdown): interrupt the turn
    at once instead of leaving it for the lease to expire. Only the owner can."""
    return await _interrupt(db, turn_id, Turn.owner_token == owner_token, reason=reason)


async def interrupt_stale_turns(db: AsyncSession, *, stale_after: timedelta) -> list[int]:
    """Interrupt every running turn whose heartbeat is older than `stale_after`.
    Returns the ids this call interrupted (a turn another caller interrupted
    first is not listed)."""
    cutoff = utcnow() - stale_after
    stale = list(
        (
            await db.execute(
                select(Turn.id).where(Turn.status == "running", Turn.heartbeat_at < cutoff)
            )
        ).scalars()
    )
    await db.rollback()  # end the read transaction before each write one
    interrupted = []
    for turn_id in stale:
        if await interrupt_turn(db, turn_id, stale_before=cutoff):
            interrupted.append(turn_id)
    return interrupted


# --- approvals ---------------------------------------------------------------


def _turn_is_running():
    """A correlated test, inside the same statement, that the approval's turn is
    still running."""
    return exists().where(Turn.id == Approval.turn_id, Turn.status == "running")


async def create_approval(
    db: AsyncSession,
    *,
    thread_id: int,
    turn_id: int,
    request_id: str,
    tool: str,
    input: dict,
    summary: str | None = None,
    kind: str | None = None,
    owner_token: str | None = None,
) -> Approval:
    """Record a question about one tool call and its `approval.requested` event.
    Raises TurnNotRunning if the turn is not running (and, when `owner_token`
    is given, not owned by it)."""
    await begin_write(db)
    still_running = [Turn.id == turn_id, Turn.thread_id == thread_id, Turn.status == "running"]
    if owner_token is not None:
        still_running.append(Turn.owner_token == owner_token)
    running = (await db.execute(select(Turn.id).where(*still_running))).scalar_one_or_none()
    if running is None:
        await db.rollback()
        raise TurnNotRunning(turn_id)
    approval = Approval(
        id=uuid4().hex,
        thread_id=thread_id,
        turn_id=turn_id,
        request_id=request_id,
        tool=tool,
        input=input,
        summary=summary,
        kind=kind,
        status="pending",
    )
    db.add(approval)
    await db.flush()
    await _add_event(
        db,
        thread_id,
        "approval.requested",
        {"approval_id": approval.id, "tool": tool, "kind": kind, "summary": summary, "input": input},
        turn_id,
    )
    await db.commit()
    return approval


async def decide_approval(
    db: AsyncSession, approval_id: str, decision: str, *, by: dict | None = None
) -> bool:
    """The person's decision. One statement: it wins only if the approval is
    still pending AND its turn is still running. False means nothing changed
    (decided already, cancelled, or the turn is no longer running) and the
    caller must answer 409 and authorize nothing. `by` is added to the
    `approval.resolved` event when something other than a person decided
    (for example {"by": "saved_rule", "rule_id": 3}), so the chat can say so."""
    _check(decision, DECISIONS, "decision")
    await begin_write(db)
    updated = await db.execute(
        update(Approval)
        .where(Approval.id == approval_id, Approval.status == "pending", _turn_is_running())
        .values(status=decision, decided_at=utcnow())
        .returning(Approval.thread_id, Approval.turn_id)
        .execution_options(synchronize_session=False)
    )
    row = updated.one_or_none()
    if row is None:
        await db.rollback()
        return False
    thread_id, turn_id = row
    await _add_event(
        db, thread_id, "approval.resolved", {**(by or {}), "approval_id": approval_id, "status": decision}, turn_id
    )
    await db.commit()
    return True


async def expire_approval(db: AsyncSession, approval_id: str) -> bool:
    """Nobody answered in time. Expiry authorizes nothing, so it does not need
    the turn to be running; it only needs the approval to still be pending."""
    return await _close_pending(db, approval_id, "expired")


async def cancel_approval(db: AsyncSession, approval_id: str) -> bool:
    """The provider withdrew the question (the tool call was abandoned). Like
    expiry it authorizes nothing, and only needs the approval to be pending."""
    return await _close_pending(db, approval_id, "cancelled")


async def _close_pending(db: AsyncSession, approval_id: str, status: str) -> bool:
    await begin_write(db)
    updated = await db.execute(
        update(Approval)
        .where(Approval.id == approval_id, Approval.status == "pending")
        .values(status=status, decided_at=utcnow())
        .returning(Approval.thread_id, Approval.turn_id)
        .execution_options(synchronize_session=False)
    )
    row = updated.one_or_none()
    if row is None:
        await db.rollback()
        return False
    thread_id, turn_id = row
    await _add_event(db, thread_id, "approval.resolved", {"approval_id": approval_id, "status": status}, turn_id)
    await db.commit()
    return True


async def mark_allow_delivered(db: AsyncSession, approval_id: str, owner_token: str) -> bool:
    """Called by the owning run immediately before it writes an Allow to the
    provider. One statement that re-checks, atomically, that the allow is still
    undelivered and that the turn is still running under this owner_token. The
    run may send the Allow only if this returns True; otherwise it has lost the
    turn and must stop."""
    await begin_write(db)
    owned = exists().where(
        Turn.id == Approval.turn_id, Turn.status == "running", Turn.owner_token == owner_token
    )
    result = await db.execute(
        update(Approval)
        .where(
            Approval.id == approval_id,
            Approval.status == "allowed",
            Approval.delivered_at.is_(None),
            owned,
        )
        .values(delivered_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount == 1


async def _cancel_open_approvals(db: AsyncSession, thread_id: int, turn_id: int) -> None:
    """Cancel the turn's approvals that authorize nothing yet: pending ones, and
    allowed ones that were never delivered. Part of the caller's transaction.
    Each is a conditional update, so it cannot undo a delivered Allow."""
    open_now = and_(
        Approval.turn_id == turn_id,
        or_(
            Approval.status == "pending",
            and_(Approval.status == "allowed", Approval.delivered_at.is_(None)),
        ),
    )
    ids = list((await db.execute(select(Approval.id).where(open_now).order_by(Approval.created_at, Approval.id))).scalars())
    for approval_id in ids:
        result = await db.execute(
            update(Approval)
            .where(Approval.id == approval_id, open_now)
            .values(status="cancelled", decided_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            await _add_event(
                db, thread_id, "approval.resolved", {"approval_id": approval_id, "status": "cancelled"}, turn_id
            )

