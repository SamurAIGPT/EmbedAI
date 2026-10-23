"""Durable threads: conversations that survive a refresh and a restart.

Every route here runs a process on this machine or reads what one did, so the
local access check is applied to the whole router, not route by route: a route
added later cannot forget it. Today every thread is local; when a non-local
provider exists the check will depend on the thread's provider, with no URL
change.

Errors a client acts on carry a machine-readable code:
`{"detail": {"error_code": "...", "message": "..."}}`.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas.thread import (
    ApprovalAnswerRequest,
    ApprovalAnswerResponse,
    ApprovalResponse,
    EventsPageResponse,
    StopResponse,
    ThreadCreateRequest,
    ThreadDetailResponse,
    ThreadResponse,
    ThreadUpdateRequest,
    TurnCreateRequest,
    TurnResponse,
)
from app.services import approval_allowlist, attachments
from app.services.attachments import AttachmentError
from app.services import thread_store as store
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import (
    NoRunningTurn,
    ProviderUnavailable,
    SessionBroken,
    SessionNotResettable,
    ThreadRuntime,
    TurnNotHere,
    UnknownProvider,
    AgentArchived,
    AgentProviderMismatch,
    EngineModelInvalid,
    ImagesUnsupported,
    UnknownAgent,
)
from app.services.thread_runtime import runtime as default_runtime
from app.services.thread_stream import event_frames

router = APIRouter(
    prefix="/threads",
    tags=["Threads"],
    dependencies=[Depends(require_local_access)],
)


def get_runtime() -> ThreadRuntime:
    return default_runtime


async def _with_computers(rt: ThreadRuntime, threads: list) -> list[ThreadResponse]:
    """Threads as responses, each with the computer its agent has (if any)."""
    from app.routers.computers import summary

    computers = await rt.computers.for_agents(sorted({t.agent_id for t in threads if t.agent_id is not None}))
    out = []
    for thread in threads:
        response = ThreadResponse.model_validate(thread)
        computer = computers.get(thread.agent_id) if thread.agent_id is not None else None
        if computer is not None:
            response = response.model_copy(update={"computer": summary(rt.computers, computer)})
        out.append(response)
    return out


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": code, "message": message})


async def _thread_or_404(rt: ThreadRuntime, thread_id: int) -> store.Thread:
    async with rt.session() as db:
        thread = await store.get_thread(db, thread_id)
    if thread is None or thread.owner_id != rt.owner_id:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


# --- threads -----------------------------------------------------------------------------


@router.post("", response_model=ThreadResponse, status_code=201)
async def create_thread(payload: ThreadCreateRequest, rt: ThreadRuntime = Depends(get_runtime)):
    try:
        thread = await rt.create_thread(
            # Omitted means the agent's provider, or Claude without an agent.
            provider=payload.provider if "provider" in payload.model_fields_set else None,
            model=payload.model,
            title=payload.title,
            agent_id=payload.agent_id,
        )
    except UnknownProvider:
        raise _error(422, "unknown_provider", f"Unknown engine: {payload.provider}") from None
    except EngineModelInvalid as exc:
        raise _error(422, "unknown_model", str(exc)) from None
    except UnknownAgent:
        raise _error(422, "agent_not_found", "No such agent.") from None
    except AgentArchived:
        raise _error(409, "agent_archived", "This agent is archived; unarchive it first.") from None
    except AgentProviderMismatch:
        raise _error(
            422, "agent_provider_mismatch", "The provider must match the agent's, or be omitted."
        ) from None
    except (OSError, ValueError) as exc:
        raise _error(409, "workspace_unavailable", str(exc)) from None
    return (await _with_computers(rt, [thread]))[0]


@router.get("", response_model=list[ThreadResponse])
async def list_threads(
    include_archived: bool = False,
    kind: Literal["chat", "all", "job", "room", "room_member", "team_notices"] = "chat",
    limit: int = Query(100, ge=1, le=500),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """Newest activity first."""
    async with rt.session() as db:
        threads = await store.list_threads(
            db, rt.owner_id, include_archived=include_archived, limit=limit, kind=kind
        )
    return await _with_computers(rt, threads)


@router.get("/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(thread_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    thread = await _thread_or_404(rt, thread_id)
    async with rt.session() as db:
        turns = await store.list_turns(db, thread_id)
    running = next((turn.id for turn in turns if turn.status == "running"), None)
    detail = (await _with_computers(rt, [thread]))[0].model_dump()
    return ThreadDetailResponse(
        **detail,
        turns=[TurnResponse.model_validate(turn) for turn in turns],
        active_turn_id=running,
    )


@router.patch("/{thread_id}", response_model=ThreadResponse)
async def update_thread(
    thread_id: int, payload: ThreadUpdateRequest, rt: ThreadRuntime = Depends(get_runtime)
):
    """Rename, archive and/or change the model. Archiving hides a conversation
    from the default list and deletes nothing; it is refused while a turn is
    running. A new model is checked against the conversation's engine (422
    `unknown_model`) and applies from the next message."""
    current = await _thread_or_404(rt, thread_id)
    if "model" in payload.model_fields_set:
        try:
            rt.check_engine_model(current.provider, payload.model)
        except UnknownProvider:
            raise _error(409, "unknown_provider", f"No provider is registered for {current.provider}.") from None
        except EngineModelInvalid as exc:
            raise _error(422, "unknown_model", str(exc)) from None
    try:
        thread = None
        # Archiving first: if it is refused, nothing has been renamed either.
        if payload.archived is not None:
            async with rt.session() as db:
                thread = await store.set_archived(db, thread_id, payload.archived)
        if payload.title is not None:
            async with rt.session() as db:
                thread = await store.rename_thread(db, thread_id, payload.title)
        if "model" in payload.model_fields_set:
            async with rt.session() as db:
                thread = await store.set_model(db, thread_id, payload.model)
    except store.ThreadNotFound:
        raise HTTPException(status_code=404, detail="Thread not found")
    except store.ThreadBusy:
        raise _error(409, "turn_running", "Stop the running answer before archiving this conversation.")
    rt.hub.notify(thread_id)
    return thread


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(thread_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Delete this app's records of a conversation: its turns, events and
    approvals in the database. Refused while a turn is running. Not a secure
    erase. Files attached to its messages are removed; nothing else is touched:
    the Claude CLI's own copy of the session under `~/.claude` stays, as do
    files Claude wrote in its workspace."""
    await _thread_or_404(rt, thread_id)
    try:
        async with rt.session() as db:
            await store.delete_thread(db, thread_id)
    except store.ThreadNotFound:
        raise HTTPException(status_code=404, detail="Thread not found")
    except store.ThreadBusy:
        raise _error(409, "turn_running", "Stop the running answer before deleting this conversation.")
    rt.hub.close(thread_id)
    attachments.remove_thread(thread_id)


# --- turns --------------------------------------------------------------------------------


@router.post("/{thread_id}/turns", response_model=TurnResponse, status_code=202)
async def send_turn(
    thread_id: int, payload: TurnCreateRequest, rt: ThreadRuntime = Depends(get_runtime)
):
    """Send a message. The answer arrives as events (stream or poll); this returns
    as soon as the turn has started."""
    thread = await _thread_or_404(rt, thread_id)
    if thread.kind in ("room", "room_member"):
        raise _error(409, thread.kind + "_thread", "Talk to this agent in the room.")
    try:
        return await rt.send_turn(
            thread_id, payload.prompt_text, attachment_ids=payload.attachment_ids
        )
    except AttachmentError as error:
        raise _error(error.status, error.code, error.message)
    except ImagesUnsupported as exc:
        raise _error(
            422,
            "images_unsupported",
            f"{exc} can't read images. Remove the image, or start a conversation on an engine that can.",
        )
    except store.ThreadNotFound:
        raise HTTPException(status_code=404, detail="Thread not found")
    except store.ThreadBusy:
        raise _error(409, "turn_running", "A turn is already running in this thread.")
    except SessionBroken:
        raise _error(
            409,
            "session_broken",
            "This conversation's session could not be continued. Start a new session to go on.",
        )
    except ProviderUnavailable as exc:
        raise _error(503, "provider_unavailable", str(exc))
    except UnknownProvider as exc:
        raise _error(409, "unknown_provider", f"No provider is registered for {exc}.")


@router.post("/{thread_id}/stop", response_model=StopResponse, status_code=202)
async def stop_turn(thread_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Stop the running answer. Claude is stopped, what it had written is kept
    (marked unfinished), anything it was waiting to ask is cancelled, and the
    conversation carries on with the same session. The end arrives as a
    `turn.cancelled` event."""
    await _thread_or_404(rt, thread_id)
    try:
        return StopResponse(turn_id=await rt.stop_turn(thread_id))
    except NoRunningTurn:
        raise _error(409, "no_running_turn", "Nothing is running in this conversation.")
    except TurnNotHere:
        raise _error(
            409,
            "turn_not_stoppable",
            "This answer is running in another process, so it cannot be stopped from here. "
            "It ends by itself, or is marked stopped within a minute if that process is gone.",
        )


@router.post("/{thread_id}/session/reset", response_model=ThreadResponse)
async def reset_session(thread_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Start the provider session over. The stored transcript stays visible; the
    provider no longer has it as context. The way out of a `broken` thread."""
    await _thread_or_404(rt, thread_id)
    try:
        return await rt.reset_session(thread_id)
    except store.ThreadNotFound:
        raise HTTPException(status_code=404, detail="Thread not found")
    except store.ThreadBusy:
        raise _error(409, "turn_running", "A turn is running; wait for it to finish first.")
    except SessionNotResettable:
        raise _error(409, "no_session", "This provider keeps no session, so there is nothing to reset.")
    except UnknownProvider as exc:
        raise _error(409, "unknown_provider", f"No provider is registered for {exc}.")


# --- events -------------------------------------------------------------------------------


@router.get("/{thread_id}/events", response_model=EventsPageResponse)
async def list_events(
    thread_id: int,
    after: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """A page of stored events with seq greater than `after`, ascending."""
    await _thread_or_404(rt, thread_id)
    async with rt.session() as db:
        events = await store.list_events(db, thread_id, after=after, limit=limit + 1)
    has_more = len(events) > limit
    events = events[:limit]
    return EventsPageResponse(
        events=events, next_after=events[-1].seq if events else after, has_more=has_more
    )


@router.get("/{thread_id}/events/stream")
async def stream_events(
    thread_id: int,
    request: Request,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(default=None),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """Server-sent events for one thread, until the client disconnects. Stored
    events carry `id: <seq>`; reconnecting with `Last-Event-ID` (a browser does
    this by itself) returns exactly the events after it. `text.delta` frames are
    typing previews: no id, never stored, superseded by the stored message."""
    await _thread_or_404(rt, thread_id)
    cursor = after
    if last_event_id is not None and last_event_id.strip():
        try:
            cursor = int(last_event_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an event number")
        if cursor < 0:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an event number")
    return StreamingResponse(
        event_frames(thread_id, cursor=cursor, hub=rt.hub, session_factory=rt.session),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# --- approvals ----------------------------------------------------------------------------


@router.get("/{thread_id}/approvals", response_model=list[ApprovalResponse])
async def list_approvals(
    thread_id: int,
    status: Literal["pending", "allowed", "denied", "expired", "cancelled"] | None = None,
    rt: ThreadRuntime = Depends(get_runtime),
):
    """Tool calls asked about, with the exact input each would run with. Use
    `?status=pending` for the ones waiting for an answer."""
    await _thread_or_404(rt, thread_id)
    async with rt.session() as db:
        return await store.list_approvals(db, thread_id, status=status)


@router.post(
    "/{thread_id}/approvals/{approval_id}",
    response_model=ApprovalAnswerResponse,
    # `rule_id` and `remember_error` appear only when they say something.
    response_model_exclude_none=True,
)
async def answer_approval(
    thread_id: int,
    approval_id: str,
    payload: ApprovalAnswerRequest,
    rt: ThreadRuntime = Depends(get_runtime),
):
    """Allow or deny one tool call. The first answer wins. An approval already
    answered, cancelled or expired, or whose turn is no longer running, answers
    409 and authorizes nothing."""
    await _thread_or_404(rt, thread_id)
    async with rt.session() as db:
        approval = await store.get_approval(db, approval_id)
    if approval is None or approval.thread_id != thread_id:
        raise HTTPException(status_code=404, detail="Approval not found")
    if not await rt.answer_approval(approval_id, payload.decision, remember=payload.remember):
        raise _error(
            409, "approval_not_waiting", "That request is no longer waiting for an answer."
        )
    if not (payload.remember and payload.decision == "allow"):
        return ApprovalAnswerResponse(decision=payload.decision)
    async with rt.session() as db:
        thread = await store.get_thread(db, thread_id)
        rule, why = await approval_allowlist.remembered_rule(
            db, rt.owner_id, approval.tool, approval.input, thread.workspace_path if thread else None
        )
    return ApprovalAnswerResponse(
        decision=payload.decision, rule_id=rule.id if rule else None, remember_error=why
    )
