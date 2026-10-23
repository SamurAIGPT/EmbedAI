"""Admin-only control leases and authenticated, ticket-bound desktop viewing."""
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.auth.middleware import principal_of
from app.computers.provider import ComputerError
from app.computers.viewer import Viewers, bridge
from app.db.models.auth import AuthSession
from app.routers.computers import _computer_or_404, _error, _refused, get_runtime
from app.services.local_access_policy import get_local_access_policy, LocalAccessDenied, require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(tags=["Computer viewer"])


def viewers(service):
    if not hasattr(service, "viewers"):
        service.viewers = Viewers()
    return service.viewers


def identity(connection):
    p = principal_of(connection.scope)
    return (p.kind, p.session_id) if p else ("loopback", None)


async def desktop(service, computer_id, *, ready=False):
    computer = await _computer_or_404(service, computer_id)
    if computer.kind != "desktop":
        raise _error(409, "not_a_desktop", "This computer has no screen.")
    if ready:
        health, status = await service.live_status(computer)
        if not health.available: raise _error(503, "docker_unavailable", health.error or "Docker is unavailable.")
        if not status or status.state != "running": raise _error(409, "computer_not_running", "Start the computer first.")
        if not status.desktop_ready: raise _error(409, "desktop_not_ready", "The desktop is starting.")
    return computer


class ControlRequest(BaseModel):
    action: Literal["take", "release"]
    lease_id: str | None = Field(default=None, min_length=64, max_length=64)
    start: bool = False


class HeartbeatRequest(BaseModel):
    lease_id: str = Field(min_length=64, max_length=64)


class TicketRequest(HeartbeatRequest):
    mode: Literal["control"] = "control"


@router.get("/computers/{computer_id}/control", dependencies=[Depends(require_local_access)])
async def get_control(computer_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    await desktop(rt.computers, computer_id)
    rt.computers.expire_control()
    return rt.computers.control.snapshot(computer_id)


@router.post("/computers/{computer_id}/control", dependencies=[Depends(require_local_access)])
async def control(computer_id: int, payload: ControlRequest, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    service = rt.computers
    computer = await desktop(service, computer_id)
    service.expire_control()
    if payload.action == "release":
        released = service.release_control(computer_id, payload.lease_id)
        if released: await viewers(service).close_computer(computer_id)
        return {**service.control.snapshot(computer_id), "released": released}
    if payload.start:
        try: computer = await service.start(computer_id)
        except ComputerError as exc: raise _refused(exc) from None
    await desktop(service, computer_id, ready=True)
    snapshot, owned, acquired, token = service.control.take(computer_id, payload.lease_id)
    result = {**snapshot, "owned": owned}
    if owned:
        await service._call(service.write_control, computer_id)
        lease = token or payload.lease_id
        result.update(ticket=viewers(service).issue(computer_id, lease, identity(request)), ticket_expires_in=30)
        if acquired: result["lease_id"] = token
    return result


@router.post("/computers/{computer_id}/control/heartbeat", dependencies=[Depends(require_local_access)])
async def heartbeat(computer_id: int, payload: HeartbeatRequest, rt: ThreadRuntime = Depends(get_runtime)):
    service = rt.computers
    await desktop(service, computer_id)
    service.expire_control()
    if not service.control.heartbeat(computer_id, payload.lease_id):
        raise _error(409, "lease_lost", "This view no longer holds control.")
    await service._call(service.write_control, computer_id)
    return Response(status_code=204)


@router.post("/computers/{computer_id}/viewer-ticket", dependencies=[Depends(require_local_access)])
async def viewer_ticket(computer_id: int, payload: TicketRequest, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    service = rt.computers
    await desktop(service, computer_id, ready=True)
    service.expire_control()
    if not service.control.owns(computer_id, payload.lease_id): raise _error(409, "lease_lost", "Take control first.")
    try: token = viewers(service).issue(computer_id, payload.lease_id, identity(request))
    except ValueError as exc: raise _error(429, "too_many_viewers", str(exc)) from None
    return {"ticket": token, "ticket_expires_in": 30}


@router.websocket("/computers/{computer_id}/viewer")
async def viewer(websocket: WebSocket, computer_id: int, rt: ThreadRuntime = Depends(get_runtime), policy=Depends(get_local_access_policy)):
    service = rt.computers
    manager = viewers(service)
    try:
        policy.authorize(websocket)
        ticket = manager.consume(websocket.query_params.get("ticket", ""), computer_id, identity(websocket))
        if not ticket:
            await websocket.close(code=4403); return
        computer = await desktop(service, computer_id, ready=True)
        service.expire_control()
        if not service.control.owns(computer_id, ticket.lease):
            await websocket.close(code=4409); return
        ident = manager.reserve(computer_id, websocket)
        if ident is None:
            await websocket.close(code=4429); return
    except (LocalAccessDenied, Exception) as exc:
        # HTTP dependency errors cannot be sent on a WebSocket connection.
        await websocket.close(code=1008); return
    service.control.viewer_opened(computer_id, ident)
    container = computer.container_id
    principal = principal_of(websocket.scope)
    async def valid():
        service.expire_control()
        if not service.control.owns(computer_id, ticket.lease): return False
        if principal and principal.session_id:
            async with rt.session() as db:
                session = await db.get(AuthSession, principal.session_id)
                if session is None or session.revoked_at or session.expires_at <= datetime.now(UTC):
                    service.release_control(computer_id, ticket.lease)
                    return False
        try:
            current = await service.get(computer_id)
            _, status = await service.live_status(current)
            return bool(status and status.state == "running" and status.container_id == container)
        except Exception: return False
    try:
        await websocket.accept(subprotocol="binary" if "binary" in websocket.scope.get("subprotocols", []) else None)
        await bridge(websocket, service.spec(computer), getattr(service.provider(computer.provider), "_docker", None), valid)
    except (WebSocketDisconnect, OSError): pass
    finally:
        manager.active.pop(ident, None)
        service.control.viewer_closed(computer_id, ident)
        try: await websocket.close()
        except Exception: pass
