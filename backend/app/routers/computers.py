"""Agent computers (docs/COMPUTERS.md): an isolated machine per agent, where its
turns run their commands and file edits instead of on this machine.

Making and starting a computer runs Docker on this machine, so the local access
check covers the whole router, as for agents and threads.

Errors a client acts on carry a machine-readable code:
`{"detail": {"error_code": "...", "message": "..."}}`.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

from app.computers import docker_cli
from app.computers.provider import ComputerError, Health, Status
from app.computers.service import ComputerNotFound, ComputerService
from app.db.models import Agent, Computer
from app.schemas.computer import (
    ComputerCreateRequest,
    ComputerDeleteResponse,
    ComputerHealthResponse,
    ComputerResponse,
    ComputerSummary,
    ComputerUpdateRequest,
    LastCommandResponse,
)
from app.services import agent_store, local_claude_runner
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime
from app.services.thread_runtime import runtime as default_runtime

router = APIRouter(tags=["Computers"], dependencies=[Depends(require_local_access)])

# Which HTTP status a provider refusal becomes.
_STATUS = {
    "docker_missing": 503,
    "docker_unavailable": 503,
    "docker_timeout": 503,
    "docker_failed": 502,
    "unknown_provider": 422,
}


def get_runtime() -> ThreadRuntime:
    return default_runtime


def _service(rt: ThreadRuntime) -> ComputerService:
    return rt.computers


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": code, "message": message})


def _refused(exc: ComputerError) -> HTTPException:
    return _error(_STATUS.get(exc.code, 409), exc.code, str(exc))


def summary(service: ComputerService, computer: Computer) -> ComputerSummary:
    return ComputerSummary(
        id=computer.id,
        provider=computer.provider,
        name=docker_cli.container_name(service.instance(), computer.id),
        image=computer.image,
        network=computer.network,
        state=computer.state,
    )


def _health(health: Health | None) -> ComputerHealthResponse | None:
    if health is None:
        return None
    return ComputerHealthResponse(
        provider=health.provider, available=health.available, version=health.version, error=health.error
    )


def respond(
    service: ComputerService, computer: Computer, *, health: Health | None = None, status: Status | None = None
) -> ComputerResponse:
    last = service.last_command(computer.id)
    return ComputerResponse(
        **summary(service, computer).model_dump(),
        agent_id=computer.agent_id,
        image_pinned=docker_cli.image_is_pinned(computer.image),
        image_id=computer.image_id,
        cpus=computer.cpu_millis / 1000,
        memory_mb=computer.memory_mb,
        pids_limit=computer.pids_limit,
        idle_minutes=computer.idle_minutes,
        auto_start=computer.auto_start,
        live_state=status.state if status else None,
        needs_recreate=bool(status and status.needs_recreate),
        problem=status.problem if status else None,
        last_error=computer.last_error,
        workspace_path=computer.workspace_path,
        guest_workspace=docker_cli.GUEST_WORKSPACE,
        container_id=computer.container_id,
        busy=service.busy(computer.id),
        last_used_at=computer.last_used_at,
        last_command=LastCommandResponse(**{k: v for k, v in last.items() if k in LastCommandResponse.model_fields}) if last else None,
        health=_health(health),
        created_at=computer.created_at,
        updated_at=computer.updated_at,
    )


async def _computer_or_404(service: ComputerService, computer_id: int) -> Computer:
    try:
        return await service.get(computer_id)
    except ComputerNotFound:
        raise HTTPException(status_code=404, detail="Computer not found") from None


async def _agent_or_404(rt: ThreadRuntime, agent_id: int) -> Agent:
    async with rt.session() as db:
        agent = await agent_store.get_agent(db, agent_id)
    if agent is None or agent.owner_id != rt.owner_id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def agent_workspace(agent: Agent) -> str:
    """The folder a thread of this agent gets, spelled as the thread stores it."""
    if agent.workspace_path:
        folder = Path(agent.workspace_path)
    else:
        folder = local_claude_runner.workspace_dir() / "agents" / str(agent.id)
        folder.mkdir(parents=True, exist_ok=True)
    return str(folder.resolve())


async def _live(service: ComputerService, computer: Computer) -> ComputerResponse:
    health, status = await service.live_status(computer)
    try:
        computer = await service.get(computer.id)
    except ComputerNotFound:
        raise HTTPException(status_code=404, detail="Computer not found") from None
    return respond(service, computer, health=health, status=status)


# --- health and listing ------------------------------------------------------------------


@router.get("/computers/health", response_model=ComputerHealthResponse)
async def computers_health(rt: ThreadRuntime = Depends(get_runtime)):
    """Whether Docker answers. Never fails: a missing or stopped Docker is
    reported, so a page can say so and keep working."""
    return _health(await _service(rt).health(fresh=True))


@router.get("/computers", response_model=list[ComputerResponse])
async def list_computers(rt: ThreadRuntime = Depends(get_runtime)):
    service = _service(rt)
    return [respond(service, computer) for computer in await service.list_all()]


@router.get("/computers/{computer_id}", response_model=ComputerResponse)
async def get_computer(computer_id: int, live: bool = True, rt: ThreadRuntime = Depends(get_runtime)):
    """With `live` (the default) the provider is asked what it sees now."""
    service = _service(rt)
    computer = await _computer_or_404(service, computer_id)
    return await _live(service, computer) if live else respond(service, computer)


# --- an agent's computer -----------------------------------------------------------------


@router.get("/agents/{agent_id}/computer", response_model=ComputerResponse)
async def get_agent_computer(agent_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    await _agent_or_404(rt, agent_id)
    service = _service(rt)
    computer = await service.for_agent(agent_id)
    if computer is None:
        raise _error(404, "no_computer", "This agent has no computer.")
    return await _live(service, computer)


@router.post("/agents/{agent_id}/computer", response_model=ComputerResponse, status_code=201)
async def create_agent_computer(
    agent_id: int, payload: ComputerCreateRequest, response: Response, rt: ThreadRuntime = Depends(get_runtime)
):
    """Give the agent a computer. Idempotent: an agent that already has one
    gets it back unchanged (200). From then on the agent's turns run their
    tools in it, and its host tools are off. With `start`, the machine is
    made and started now. Only an agent whose engine can run its tools in a
    computer can have one (409 `engine_without_computers`)."""
    agent = await _agent_or_404(rt, agent_id)
    try:
        supports = rt.engines.capabilities(agent.provider).computers
    except Exception:  # noqa: BLE001  (an engine this app no longer has)
        supports = False
    if not supports:
        raise _error(
            409,
            "engine_without_computers",
            f"This agent's engine ({agent.provider}) cannot run its tools in a computer. "
            "Computers work with Claude for now.",
        )
    service = _service(rt)
    settings = payload.model_dump(exclude={"start", "image"}, exclude_none=True)
    try:
        computer, created = await service.create_for_agent(
            agent, workspace=agent_workspace(agent), image=payload.image, **settings
        )
        if not created:
            response.status_code = 200
        if payload.start:
            computer = await service.start(computer.id)
    except ValueError as exc:
        raise _error(422, "invalid_computer", str(exc)) from None
    except ComputerError as exc:
        raise _refused(exc) from None
    return await _live(service, computer)


# --- one computer ------------------------------------------------------------------------


@router.patch("/computers/{computer_id}", response_model=ComputerResponse)
async def update_computer(computer_id: int, payload: ComputerUpdateRequest, rt: ThreadRuntime = Depends(get_runtime)):
    """CPU, memory and process limits apply to a live machine at once; the
    image and network on the next recreate (`needs_recreate` says so)."""
    service = _service(rt)
    await _computer_or_404(service, computer_id)
    try:
        computer = await service.update_config(computer_id, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise _error(422, "invalid_computer", str(exc)) from None
    except ComputerNotFound:
        raise HTTPException(status_code=404, detail="Computer not found") from None
    return await _live(service, computer)


async def _lifecycle(rt: ThreadRuntime, computer_id: int, step) -> ComputerResponse:
    service = _service(rt)
    await _computer_or_404(service, computer_id)
    try:
        computer = await step(service)
    except ComputerError as exc:
        raise _refused(exc) from None
    except ComputerNotFound:
        raise HTTPException(status_code=404, detail="Computer not found") from None
    return await _live(service, computer)


@router.post("/computers/{computer_id}/prepare-image", response_model=ComputerResponse)
async def prepare_image(computer_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Download the computer's image. The only step that downloads anything."""
    return await _lifecycle(rt, computer_id, lambda s: s.prepare_image(computer_id))


@router.post("/computers/{computer_id}/start", response_model=ComputerResponse)
async def start_computer(computer_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Make the machine if it does not exist yet (from an image already on this
    computer), then start it."""
    return await _lifecycle(rt, computer_id, lambda s: s.start(computer_id))


@router.post("/computers/{computer_id}/stop", response_model=ComputerResponse)
async def stop_computer(computer_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    return await _lifecycle(rt, computer_id, lambda s: s.stop(computer_id))


@router.post("/computers/{computer_id}/recreate", response_model=ComputerResponse)
async def recreate_computer(computer_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Remove the machine and make it again from the current settings and the
    agent's current folder. Files in the folder stay; everything else inside
    the machine is lost."""
    service = _service(rt)
    computer = await _computer_or_404(service, computer_id)
    async with rt.session() as db:
        agent = await agent_store.get_agent(db, computer.agent_id)
    workspace = agent_workspace(agent) if agent is not None else None
    return await _lifecycle(rt, computer_id, lambda s: s.recreate(computer_id, workspace=workspace))


@router.delete("/computers/{computer_id}", response_model=ComputerDeleteResponse)
async def delete_computer(computer_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    """Remove the computer. The agent's turns go back to running on this
    machine. Files in its folder are never touched."""
    service = _service(rt)
    await _computer_or_404(service, computer_id)
    try:
        removed = await service.delete(computer_id)
    except ComputerError as exc:
        raise _refused(exc) from None
    except ComputerNotFound:
        raise HTTPException(status_code=404, detail="Computer not found") from None
    return ComputerDeleteResponse(removed=removed)
