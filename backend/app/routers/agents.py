"""Named agents: reusable instructions, model, workspace and approval mode that
a conversation can be started from (F06).

An agent only shapes what a local `claude` run is told and where it runs, so
the local access check is applied to the whole router, exactly as for threads.

Errors a client acts on carry a machine-readable code:
`{"detail": {"error_code": "...", "message": "..."}}`.
"""

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
)
from app.services import agent_memory, agent_package
from app.services import agent_store as store
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime
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


async def _agent_or_404(rt: ThreadRuntime, agent_id: int) -> store.Agent:
    async with rt.session() as db:
        agent = await store.get_agent(db, agent_id)
    if agent is None or agent.owner_id != rt.owner_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(payload: AgentCreateRequest, rt: ThreadRuntime = Depends(get_runtime)):
    if payload.provider not in rt.adapters:
        raise HTTPException(status_code=422, detail=f"Unknown provider: {payload.provider}")
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
    stays as it was when it began."""
    await _agent_or_404(rt, agent_id)
    changes = {
        field: getattr(payload, field)
        for field in (
            "name",
            "title",
            "description",
            "instructions",
            "model",
            "workspace_path",
            "approval_mode",
            "pinned",
            "memory_enabled",
        )
        if field in payload.model_fields_set
    }
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
    return _memory_response(agent, doc, redacted)


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
    document, redacted = agent_package.export_package(agent, notes)
    return AgentExportResponse(
        file_name=agent_package.file_name(agent.name), document=document, redacted=redacted
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
    _start_notes(agent.id, parsed["notes"] if payload.include_notes else None)
    return agent

