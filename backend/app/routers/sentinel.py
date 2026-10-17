"""Sentinel: a read-only health scan of this machine's conversations and
decision log (see services/sentinel.py). Local access only; it changes
nothing."""

from fastapi import APIRouter, Depends, Query

from app.routers.threads import get_runtime
from app.schemas.approvals import SentinelReport
from app.services import sentinel
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(
    prefix="/sentinel",
    tags=["Sentinel"],
    dependencies=[Depends(require_local_access)],
)


@router.get("/scan", response_model=SentinelReport)
async def scan(
    hours: int = Query(default=sentinel.DEFAULT_HOURS, ge=1, le=sentinel.MAX_HOURS),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """Retry loops, repeated denials, tool errors and failed turns in the last
    `hours`, worst first. Issues hold normalized titles and evidence ids,
    never raw tool output."""
    return await sentinel.scan(rt.session, rt.owner_id, hours)
