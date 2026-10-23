"""The "Needs you" card: read it, and tell the server the person is here.
Owner and admin only (not in the chat scope)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.services.thread_runtime import ThreadRuntime, runtime as default_runtime
from app.team import needs_you

router = APIRouter(prefix="/needs-you", tags=["Needs you"])

_services: dict[int, needs_you.NeedsYou] = {}


def get_runtime() -> ThreadRuntime:
    return default_runtime


def get_service(rt: ThreadRuntime = Depends(get_runtime)) -> needs_you.NeedsYou:
    """One per runtime, so the card's render guard is shared with the tick."""
    return service_for(rt)


def service_for(rt: ThreadRuntime) -> needs_you.NeedsYou:
    service = _services.get(id(rt))
    if service is None:
        service = _services[id(rt)] = needs_you.NeedsYou(rt)
    return service


class Presence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time_zone: str


@router.get("")
async def read_card(service: needs_you.NeedsYou = Depends(get_service)):
    """What needs the person now, as the card would show it."""
    return await service.read()


@router.post("/presence")
async def presence(payload: Presence, service: needs_you.NeedsYou = Depends(get_service)):
    """The person opened or used the app. Stores their time zone; on a return
    or the first visit of the day, writes the card and says where it is."""
    try:
        return await service.presence(payload.time_zone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
