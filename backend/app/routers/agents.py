"""Named agents: reusable instructions, model, workspace and approval mode that
a conversation can be started from (F06).

An agent only shapes what a local `claude` run is told and where it runs, so
the local access check is applied to the whole router, exactly as for threads.

Errors a client acts on carry a machine-readable code:
`{"detail": {"error_code": "...", "message": "..."}}`.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.agent import AgentCreateRequest, AgentResponse, AgentUpdateRequest
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
            return await store.create_agent(
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
            )
    except store.AgentNameTaken:
        raise _error(409, "agent_name_taken", "An agent with this name already exists.") from None


@router.get("", response_model=list[AgentResponse])
async def list_agents(include_archived: bool = False, rt: ThreadRuntime = Depends(get_runtime)):
    """Pinned first, then alphabetical by name."""
    async with rt.session() as db:
        return await store.list_agents(db, rt.owner_id, include_archived=include_archived)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    return await _agent_or_404(rt, agent_id)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: int, payload: AgentUpdateRequest, rt: ThreadRuntime = Depends(get_runtime)
):
    """Edit an agent. Conversations already started from it use the new
    instructions, model and approval mode from their next message; a
    conversation's own workspace stays as it was when it began."""
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
        )
        if field in payload.model_fields_set
    }
    if "workspace_path" in changes:
        changes["workspace_path"] = _check_workspace(changes["workspace_path"])
    try:
        async with rt.session() as db:
            return await store.update_agent(
                db, agent_id, archived=payload.archived, **changes
            )
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

