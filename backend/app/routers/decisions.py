"""The decision log, read-only: what Claude asked to do and how each question
was settled. Local access only. The file is written by the backend and is never
changed through the API."""

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.schemas.approvals import DecisionsPage
from app.services import decision_log
from app.services.local_access_policy import require_local_access

router = APIRouter(
    prefix="/decisions",
    tags=["Decisions"],
    dependencies=[Depends(require_local_access)],
)


@router.get("", response_model=DecisionsPage)
async def list_decisions(
    limit: int = Query(default=50, ge=1, le=decision_log.MAX_PAGE),
    before: str | None = Query(default=None, max_length=64),
    event: Literal["shown", "allowed", "denied", "auto_allowed", "expired", "cancelled"] | None = None,
    thread_id: int | None = None,
):
    """Newest first. Page with `before=<next_before>` from the previous page."""
    items, next_before = decision_log.read_page(
        limit=limit, before=before, event=event, thread_id=thread_id
    )
    return DecisionsPage(items=items, next_before=next_before)
