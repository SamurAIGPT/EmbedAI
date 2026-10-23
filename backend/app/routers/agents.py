"""Named agents: reusable instructions, model, workspace and approval mode that
a conversation can be started from (F06).

An agent only shapes what a local `claude` run is told and where it runs, so
the local access check is applied to the whole router, exactly as for threads.

Errors a client acts on carry a machine-readable code:
`{"detail": {"error_code": "...", "message": "..."}}`.
"""

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.agent import (
    AgentCreateRequest,
    AgentExportResponse,
    AgentImportRequest,
    AgentMemoryResponse,
    AgentMemoryWriteRequest,
    AgentResponse,
    AgentUpdateRequest,
    MemoryJournalRow,
    MemoryProposalAcceptRequest,
    MemoryProposalResponse,
    TopicDocResponse,
    TopicInfoResponse,
    TopicListResponse,
    TopicWriteRequest,
)
from app.services import agent_memory, agent_package, memory_entries, memory_journal, memory_log, memory_proposals, memory_topics
from app.services import agent_store as store
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import EngineModelInvalid, ThreadRuntime, UnknownProvider
from app.services.thread_runtime import runtime as default_runtime
from app.services.workspaces import require_workspace

router = APIRouter(
    prefix="/agents",
    tags=["Agents"],
    dependencies=[Depends(require_local_access)],
)


def get_runtime() -> ThreadRuntime:
    return default_runtime


async def _with_computers(rt: ThreadRuntime, agents: list) -> list[AgentResponse]:
    """Agents as responses, each with its computer (if it has one)."""
    from app.routers.computers import summary

    computers = await rt.computers.for_agents([agent.id for agent in agents])
    out = []
    for agent in agents:
        response = AgentResponse.model_validate(agent)
        computer = computers.get(agent.id)
        if computer is not None:
            response = response.model_copy(
                update={"computer_id": computer.id, "computer": summary(rt.computers, computer)}
            )
        out.append(response)
    return out


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": code, "message": message})


def _check_workspace(path: str | None) -> str | None:
    """A workspace is an existing folder this process can use. It is stored in
    its canonical spelling, as a thread's pinned workspace is."""
    if path is None:
        return None
    folder = Path(path.strip()).expanduser()
    if not folder.is_absolute():
        raise _error(422, "workspace_unavailable", "The workspace must be an absolute path.")
    try:
        return str(require_workspace(folder.resolve()))
    except OSError:
        raise _error(
            422, "workspace_unavailable", "The workspace folder does not exist or cannot be used."
        ) from None


def _start_notes(agent_id: int, notes: str | None) -> None:
    """A new agent starts with exactly the notes it was given. A file left at
    its id by an agent deleted earlier is never inherited."""
    if notes and notes.strip():
        agent_memory.write_memory(agent_id, notes)
    else:
        agent_memory.remove_memory(agent_id)


def _start_imported_notes(agent_id: int, parsed: dict | None) -> None:
    """An imported agent is new, so no notes of its own can be overwritten: it
    starts with exactly the file's notes and topic notes. Each file written is
    a journal row by `import`, so it can be undone."""
    _start_notes(agent_id, parsed["notes"] if parsed else None)
    if not parsed:
        return
    if parsed["notes"] and parsed["notes"].strip():
        memory_journal.record(
            agent_id, agent_memory.MEMORY_FILE, actor="import", before=None, after=agent_memory.read_memory(agent_id).text
        )
    for name, text in parsed["topics"].items():
        try:
            doc, _, _ = memory_topics.write_topic(agent_id, name, text)
        except (agent_memory.MemoryTooLarge, memory_topics.InvalidTopicName, memory_topics.TooManyTopics):
            continue
        memory_journal.record(agent_id, memory_journal.note_path(name), actor="import", before=None, after=doc.text)


def _check_engine(rt: ThreadRuntime, provider: str, model: str | None) -> None:
    """The engine must be registered and accept the model."""
    try:
        rt.check_engine_model(provider, model)
    except UnknownProvider:
        raise _error(422, "unknown_provider", f"Unknown engine: {provider}") from None
    except EngineModelInvalid as exc:
        raise _error(422, "unknown_model", str(exc)) from None


async def _agent_or_404(rt: ThreadRuntime, agent_id: int) -> store.Agent:
    async with rt.session() as db:
        agent = await store.get_agent(db, agent_id)
    if agent is None or agent.owner_id != rt.owner_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def _check_fallback(rt: ThreadRuntime, provider: str, fallback: str | None, model: str | None) -> None:
    """A fallback engine must be registered, accept its model and differ from
    the agent's own engine. What it must be able to do is checked per turn
    (thread_runtime.py), since that depends on the turn."""
    if fallback is None:
        if model is not None:
            raise _error(422, "invalid_fallback", "A fallback model needs a fallback engine.")
        return
    if fallback == provider:
        raise _error(422, "invalid_fallback", "The fallback engine must be another engine than the agent's own.")
    _check_engine(rt, fallback, model)


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(payload: AgentCreateRequest, rt: ThreadRuntime = Depends(get_runtime)):
    _check_engine(rt, payload.provider, payload.model)
    _check_fallback(rt, payload.provider, payload.fallback_provider, payload.fallback_model)
    workspace = _check_workspace(payload.workspace_path)
    try:
        async with rt.session() as db:
            agent = await store.create_agent(
                db,
                owner_id=rt.owner_id,
                name=payload.name,
                title=payload.title,
                description=payload.description,
                instructions=payload.instructions,
                provider=payload.provider,
                model=payload.model,
                workspace_path=workspace,
                approval_mode=payload.approval_mode,
                pinned=payload.pinned,
                memory_enabled=payload.memory_enabled,
                recall_enabled=payload.recall_enabled,
                memory_upkeep=payload.memory_upkeep,
                fallback_provider=payload.fallback_provider,
                fallback_model=payload.fallback_model,
            )
    except store.AgentNameTaken:
        raise _error(409, "agent_name_taken", "An agent with this name already exists.") from None
    _start_notes(agent.id, payload.starter_notes)
    return agent


@router.get("", response_model=list[AgentResponse])
async def list_agents(include_archived: bool = False, rt: ThreadRuntime = Depends(get_runtime)):
    """Pinned first, then alphabetical by name."""
    async with rt.session() as db:
        agents = await store.list_agents(db, rt.owner_id, include_archived=include_archived)
    return await _with_computers(rt, agents)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    return (await _with_computers(rt, [await _agent_or_404(rt, agent_id)]))[0]


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: int, payload: AgentUpdateRequest, rt: ThreadRuntime = Depends(get_runtime)
):
    """Edit an agent. Conversations already started from it use the new
    model and approval mode from their next message. New instructions and
    notes are sent with every turn, but the Claude CLI keeps the system prompt
    a session started with when it resumes it, so in practice they reach
    conversations started after the change. A conversation's own workspace
    stays as it was when it began, and so does its engine: a conversation
    started on another engine than the agent's new one keeps its engine and
    uses that engine's default model. An agent with a computer can only move
    to an engine that can use one (409 `engine_without_computers`)."""
    agent = await _agent_or_404(rt, agent_id)
    changes = {
        field: getattr(payload, field)
        for field in (
            "name",
            "title",
            "description",
            "instructions",
            "provider",
            "model",
            "workspace_path",
            "approval_mode",
            "pinned",
            "memory_enabled",
            "recall_enabled",
            "memory_upkeep",
            "fallback_provider",
            "fallback_model",
        )
        if field in payload.model_fields_set
    }
    if "fallback_provider" in changes and changes["fallback_provider"] != agent.fallback_provider:
        changes.setdefault("fallback_model", None)  # the old fallback's model means nothing to the new one
    provider = changes.get("provider", agent.provider)
    if "provider" in changes and changes["provider"] != agent.provider:
        if "model" not in changes:
            changes["model"] = None  # the old engine's model means nothing to the new one
        try:
            caps = rt.engines.capabilities(provider)
        except Exception:  # noqa: BLE001  (an unknown engine is reported just below)
            caps = None
        if caps is not None and not caps.computers and await rt.computers.for_agent(agent_id) is not None:
            raise _error(
                409,
                "engine_without_computers",
                "This agent has a computer, and that engine cannot run its tools in one. "
                "Remove the computer first, or keep an engine that supports computers.",
            )
    if "provider" in changes or "model" in changes:
        _check_engine(rt, provider, changes.get("model", agent.model if provider == agent.provider else None))
    if {"provider", "fallback_provider", "fallback_model"} & set(changes):
        _check_fallback(
            rt,
            provider,
            changes.get("fallback_provider", agent.fallback_provider),
            changes.get("fallback_model", agent.fallback_model),
        )
    if "workspace_path" in changes:
        changes["workspace_path"] = _check_workspace(changes["workspace_path"])
    try:
        async with rt.session() as db:
            agent = await store.update_agent(
                db, agent_id, archived=payload.archived, **changes
            )
        return (await _with_computers(rt, [agent]))[0]
    except store.AgentNotFound:
        raise HTTPException(status_code=404, detail="Agent not found") from None
    except store.AgentNameTaken:
        raise _error(409, "agent_name_taken", "An agent with this name already exists.") from None


@router.post("/{agent_id}/duplicate", response_model=AgentResponse, status_code=201)
async def duplicate_agent(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Copy an agent's settings under a new name. Conversations are not copied."""
    await _agent_or_404(rt, agent_id)
    try:
        async with rt.session() as db:
            return await store.duplicate_agent(db, agent_id)
    except store.AgentNotFound:
        raise HTTPException(status_code=404, detail="Agent not found") from None
    except store.AgentNameTaken:
        raise _error(409, "agent_name_taken", "Try again: that name was just taken.") from None


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Delete the agent. Its conversations stay, unlinked, and carry on with
    their own provider and model (not its instructions). Archive instead to
    keep them linked."""
    await _agent_or_404(rt, agent_id)
    try:
        async with rt.session() as db:
            await store.delete_agent(db, agent_id)
    except store.AgentNotFound:
        raise HTTPException(status_code=404, detail="Agent not found") from None
    # Its notes go with it; the folder stays, as its conversations may use it.
    rt.capture.drop_agent(agent_id)
    agent_memory.remove_memory(agent_id)
    # So does its computer (the machine; never the folder). If Docker is not
    # answering, the removal finishes later.
    try:
        await rt.computers.mark_agent_deleted(agent_id)
    except Exception:  # noqa: BLE001
        pass


# --- notes (MEMORY.md) ---------------------------------------------------------------


def _memory_response(agent, doc: agent_memory.MemoryDoc, redacted: int = 0) -> AgentMemoryResponse:
    return AgentMemoryResponse(
        path=doc.path,
        text=doc.text,
        hash=doc.hash,
        lines=doc.lines,
        bytes=doc.bytes,
        loaded_lines=doc.loaded_lines,
        loaded_bytes=doc.loaded_bytes,
        truncated=doc.truncated,
        max_lines=agent_memory.MAX_LINES,
        max_bytes=agent_memory.MAX_BYTES,
        enabled=agent.memory_enabled,
        redacted=redacted,
        expired=doc.expired,
        expired_lines=doc.expired_lines,
    )


@router.get("/{agent_id}/memory", response_model=AgentMemoryResponse)
async def read_memory(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """The agent's notes, with how much of them loads into each turn."""
    agent = await _agent_or_404(rt, agent_id)
    return _memory_response(agent, agent_memory.read_memory(agent.id))


@router.put("/{agent_id}/memory", response_model=AgentMemoryResponse)
async def write_memory(
    agent_id: int, payload: AgentMemoryWriteRequest, rt: ThreadRuntime = Depends(get_runtime)
):
    """Replace the notes. With `expected_hash` (the `hash` you read), a file
    the agent changed meanwhile is not overwritten: 409 `memory_changed`, with
    what is there now. Secrets are redacted before anything is written."""
    agent = await _agent_or_404(rt, agent_id)
    path = agent_memory.memory_path(agent.id)
    before = agent_memory.read_memory(agent.id).text if path.is_file() and not path.is_symlink() else None
    try:
        doc, redacted = agent_memory.write_memory(
            agent.id, payload.text, expected_hash=payload.expected_hash
        )
    except agent_memory.MemoryConflict as conflict:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "memory_changed",
                "message": "The notes changed since you opened them.",
                "current": conflict.current.text,
                "current_hash": conflict.current.hash,
            },
        ) from None
    except agent_memory.MemoryTooLarge as exc:
        raise _error(413, "memory_too_large", str(exc)) from None
    memory_journal.record(agent.id, agent_memory.MEMORY_FILE, actor="person", before=before, after=doc.text)
    return _memory_response(agent, doc, redacted)


# --- topic notes (memory/<topic>.md) ----------------------------------------------------


def _conflict(conflict: agent_memory.MemoryConflict, message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error_code": "memory_changed",
            "message": message,
            "current": conflict.current.text,
            "current_hash": conflict.current.hash,
        },
    )


def _topic_name(name: str) -> str:
    try:
        return memory_topics.check_name(name)
    except memory_topics.InvalidTopicName as exc:
        raise _error(422, "invalid_topic_name", str(exc)) from None


def _topic_response(doc: memory_topics.TopicDoc, redacted: int = 0) -> TopicDocResponse:
    return TopicDocResponse(
        name=doc.name, path=doc.path, text=doc.text, hash=doc.hash, bytes=doc.bytes, exists=doc.exists, redacted=redacted
    )


@router.get("/{agent_id}/memory/topics", response_model=TopicListResponse)
async def list_topics(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """The agent's topic notes, newest change first."""
    agent = await _agent_or_404(rt, agent_id)
    return TopicListResponse(
        topics=[
            TopicInfoResponse(
                name=t.name,
                title=t.title,
                aliases=list(t.aliases),
                bytes=t.bytes,
                modified_at=datetime.fromtimestamp(t.modified_at, UTC),
            )
            for t in memory_topics.list_topics(agent.id)
        ],
        max_topics=memory_topics.MAX_TOPICS,
        max_bytes=memory_topics.TOPIC_MAX_BYTES,
        folder=str(memory_topics.topics_dir(agent.id)),
    )


@router.get("/{agent_id}/memory/topics/{name}", response_model=TopicDocResponse)
async def read_topic(agent_id: int, name: str, rt: ThreadRuntime = Depends(get_runtime)):
    """One topic note; a name with no note yet reads as empty (`exists` false)."""
    agent = await _agent_or_404(rt, agent_id)
    return _topic_response(memory_topics.read_topic(agent.id, _topic_name(name)))


@router.put("/{agent_id}/memory/topics/{name}", response_model=TopicDocResponse)
async def write_topic(
    agent_id: int, name: str, payload: TopicWriteRequest, rt: ThreadRuntime = Depends(get_runtime)
):
    """Create or replace a topic note. With `expected_hash` a note changed
    meanwhile is not overwritten (409 `memory_changed`). Secrets are redacted
    before anything is written; the change is journaled."""
    agent = await _agent_or_404(rt, agent_id)
    name = _topic_name(name)
    try:
        doc, redacted, before = memory_topics.write_topic(
            agent.id, name, payload.text, expected_hash=payload.expected_hash
        )
    except agent_memory.MemoryConflict as conflict:
        raise _conflict(conflict, "The note changed since you opened it.") from None
    except agent_memory.MemoryTooLarge as exc:
        raise _error(413, "memory_too_large", str(exc)) from None
    except memory_topics.TooManyTopics as exc:
        raise _error(409, "too_many_topics", str(exc)) from None
    except memory_topics.InvalidTopicName as exc:
        raise _error(422, "invalid_topic_name", str(exc)) from None
    memory_journal.record(agent.id, memory_journal.note_path(name), actor="person", before=before, after=doc.text)
    return _topic_response(doc, redacted)


@router.delete("/{agent_id}/memory/topics/{name}", status_code=204)
async def delete_topic(
    agent_id: int, name: str, expected_hash: str | None = None, rt: ThreadRuntime = Depends(get_runtime)
):
    """Delete a topic note (the journal can bring it back)."""
    agent = await _agent_or_404(rt, agent_id)
    name = _topic_name(name)
    try:
        before = memory_topics.delete_topic(agent.id, name, expected_hash=expected_hash)
    except agent_memory.MemoryConflict as conflict:
        raise _conflict(conflict, "The note changed since you opened it.") from None
    if before is None:
        raise HTTPException(status_code=404, detail="Topic note not found")
    memory_journal.record(agent.id, memory_journal.note_path(name), actor="person", before=before, after=None)


# --- daily logs ------------------------------------------------------------------------


@router.get("/{agent_id}/memory/logs")
async def list_logs(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """The days that have a daily log (memory/log/YYYY-MM-DD.md), newest first."""
    agent = await _agent_or_404(rt, agent_id)
    return {"logs": memory_log.list_logs(agent.id)}


@router.get("/{agent_id}/memory/logs/{day}")
async def read_log(agent_id: int, day: str, rt: ThreadRuntime = Depends(get_runtime)):
    """One day's log, read-only (redacted)."""
    agent = await _agent_or_404(rt, agent_id)
    text = memory_log.read_log(agent.id, day)
    if text is None:
        raise HTTPException(status_code=404, detail="No log for that day")
    return {"day": day, "text": text}


@router.delete("/{agent_id}/memory/logs/{day}", status_code=204)
async def delete_log(agent_id: int, day: str, rt: ThreadRuntime = Depends(get_runtime)):
    agent = await _agent_or_404(rt, agent_id)
    if not memory_log.delete_log(agent.id, day):
        raise HTTPException(status_code=404, detail="No log for that day")


# --- the journal ---------------------------------------------------------------------


def _journal_row(row: dict) -> MemoryJournalRow:
    return MemoryJournalRow(
        id=row["id"],
        at=datetime.fromtimestamp(float(row.get("at") or 0), UTC),
        path=row["path"],
        actor=str(row.get("actor") or ""),
        via=row.get("via") if isinstance(row.get("via"), str) else None,
        kind=str(row.get("kind") or ""),
        before_hash=row.get("before_hash"),
        after_hash=row.get("after_hash"),
        diff=str(row.get("diff") or ""),
        added=int(row.get("added") or 0),
        removed=int(row.get("removed") or 0),
        can_undo=bool(row.get("can_undo")),
        reason=row.get("reason"),
    )


@router.get("/{agent_id}/memory/journal", response_model=list[MemoryJournalRow])
async def read_journal(agent_id: int, limit: int = 50, rt: ThreadRuntime = Depends(get_runtime)):
    """Changes made to the agent's notes through this app, newest first."""
    agent = await _agent_or_404(rt, agent_id)
    return [_journal_row(row) for row in memory_journal.read(agent.id, limit)]


@router.post("/{agent_id}/memory/journal/{entry_id}/undo", response_model=MemoryJournalRow | None)
async def undo_journal(agent_id: int, entry_id: str, rt: ThreadRuntime = Depends(get_runtime)):
    """Put the file back the way it was before that change. The undo is
    itself journaled (and can be undone)."""
    agent = await _agent_or_404(rt, agent_id)
    try:
        row = memory_journal.undo(agent.id, entry_id)
    except memory_journal.JournalEntryNotFound:
        raise HTTPException(status_code=404, detail="No such change") from None
    except memory_journal.CannotUndo as exc:
        raise _error(409, "cannot_undo", str(exc)) from None
    except memory_topics.TooManyTopics as exc:
        raise _error(409, "too_many_topics", str(exc)) from None
    return _journal_row(row) if row is not None else None


# --- proposed notes ------------------------------------------------------------------


def _proposal(p: dict) -> MemoryProposalResponse:
    return MemoryProposalResponse(
        id=p["id"],
        at=datetime.fromtimestamp(float(p.get("at") or 0), UTC),
        text=p.get("text") or "",
        line=p.get("line") or "",
        target=p.get("target") or agent_memory.MEMORY_FILE,
        thread_id=p.get("thread_id"),
        thread_title=p.get("thread_title"),
        turn_id=p.get("turn_id"),
    )


@router.get("/{agent_id}/memory/upkeep")
async def memory_upkeep_status(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Whether quiet memory upkeep is on for the agent, whether its engine can
    make the background call, and what the last capture did."""
    from app.core.config import settings
    from app.services import memory_capture, memory_settings

    agent = await _agent_or_404(rt, agent_id)
    adapter = rt.adapters.get(agent.provider)
    return {
        "enabled": bool(agent.memory_enabled and agent.memory_upkeep and settings.MEMORY_CAPTURE_QUIET_SECONDS > 0),
        "engine_can_call": hasattr(adapter, "generate_text"),
        "quiet_seconds": settings.MEMORY_CAPTURE_QUIET_SECONDS,
        "last_capture": memory_capture.read_status(agent.id).get("last_capture"),
        "last_tidy": memory_capture.read_status(agent.id).get("last_tidy"),
        "tidy_hour": memory_settings.tidy_hour(),
        "engine": agent.provider,
        "helper_model": memory_capture.helper_model(agent.provider, None),
    }


@router.post("/{agent_id}/memory/tidy")
async def tidy_now(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Run the tidy-up now (organize, expire, de-duplicate, reconcile
    contradictions). 409 while the agent is working or upkeep is off."""
    agent = await _agent_or_404(rt, agent_id)
    if not (agent.memory_enabled and agent.memory_upkeep):
        raise _error(409, "upkeep_off", "Memory upkeep is off for this agent.")
    if rt.capture.busy(agent.id):
        raise _error(409, "agent_busy", "The agent is working; try again when it is done.")
    return await rt.capture.tidy(agent.id)


@router.get("/{agent_id}/memory/proposals", response_model=list[MemoryProposalResponse])
async def list_proposals(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Notes proposed from conversations that have gone quiet, waiting for
    the person. Looking also reads any conversation that went quiet since the
    last look. Nothing is saved until a proposal is accepted."""
    agent = await _agent_or_404(rt, agent_id)
    if agent.memory_enabled:
        async with rt.session() as db:
            await memory_proposals.sweep(db, rt.owner_id, agent.id)
    return [_proposal(p) for p in memory_proposals.pending(agent.id)]


@router.post("/{agent_id}/memory/proposals/{proposal_id}/accept")
async def accept_proposal(
    agent_id: int,
    proposal_id: str,
    payload: MemoryProposalAcceptRequest | None = None,
    rt: ThreadRuntime = Depends(get_runtime),
):
    """Save the proposal (or the person's edited line) into MEMORY.md or a
    topic note. Journaled; the journal can undo it."""
    agent = await _agent_or_404(rt, agent_id)
    payload = payload or MemoryProposalAcceptRequest()
    if payload.target is not None and payload.target != agent_memory.MEMORY_FILE:
        _topic_name(payload.target)
    try:
        return memory_proposals.accept(agent.id, proposal_id, line=payload.line, target=payload.target)
    except memory_proposals.ProposalNotFound:
        raise HTTPException(status_code=404, detail="No such proposal") from None
    except agent_memory.MemoryTooLarge as exc:
        raise _error(413, "memory_too_large", str(exc)) from None
    except memory_entries.EntryTooLarge as exc:
        raise _error(422, "entry_too_large", str(exc)) from None
    except memory_topics.TooManyTopics as exc:
        raise _error(409, "too_many_topics", str(exc)) from None


@router.post("/{agent_id}/memory/proposals/{proposal_id}/reject", status_code=204)
async def reject_proposal(agent_id: int, proposal_id: str, rt: ThreadRuntime = Depends(get_runtime)):
    """Drop the proposal; the same line is not proposed again."""
    agent = await _agent_or_404(rt, agent_id)
    try:
        memory_proposals.reject(agent.id, proposal_id)
    except memory_proposals.ProposalNotFound:
        raise HTTPException(status_code=404, detail="No such proposal") from None


# --- one agent as a portable file ------------------------------------------------------


@router.get("/{agent_id}/export", response_model=AgentExportResponse)
async def export_agent(
    agent_id: int, include_notes: bool = False, rt: ThreadRuntime = Depends(get_runtime)
):
    """The agent as one file: name, title, description, instructions and, with
    `include_notes`, its notes. Never its model, folder, approval mode or
    conversations. Anything that looks like a secret is replaced, and
    `redacted` names the parts where that happened."""
    agent = await _agent_or_404(rt, agent_id)
    notes = agent_memory.read_memory(agent.id).text if include_notes else None
    topics = (
        {
            t.name: memory_topics.read_topic(agent.id, t.name).text
            for t in memory_topics.list_topics(agent.id)
            if t.name != memory_topics.ARCHIVE_NAME
        }
        if include_notes
        else None
    )
    skipped: list[str] = []
    document, redacted = agent_package.export_package(agent, notes, topics, skipped)
    return AgentExportResponse(
        file_name=agent_package.file_name(agent.name), document=document, redacted=redacted, skipped=skipped
    )


@router.post("/import", response_model=AgentResponse, status_code=201)
async def import_agent(payload: AgentImportRequest, rt: ThreadRuntime = Depends(get_runtime)):
    """A new agent from an agent file. It always starts asking first, on the
    default model, in its own new folder, whatever the file says; a name in
    use gets a number. 422 `invalid_agent_file` when the file is not one."""
    try:
        parsed = agent_package.parse_package(payload.document)
    except agent_package.PackageError as exc:
        raise _error(422, "invalid_agent_file", str(exc)) from None
    try:
        async with rt.session() as db:
            name = await store.free_name(db, rt.owner_id, parsed["name"])
            agent = await store.create_agent(
                db,
                owner_id=rt.owner_id,
                name=name,
                title=parsed["title"],
                description=parsed["description"],
                instructions=parsed["instructions"],
                approval_mode="ask",
            )
    except store.AgentNameTaken:
        raise _error(409, "agent_name_taken", "Try again: that name was just taken.") from None
    _start_imported_notes(agent.id, parsed if payload.include_notes else None)
    return agent

