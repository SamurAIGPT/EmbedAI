"""The decision log, read-only: what Claude asked to do and how each question
was settled. Local access only. The file is written by the backend and is never
changed through the API."""

from itertools import islice

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.schemas.approvals import DecisionEvent, DecisionsPage
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
    event: DecisionEvent | None = None,
    thread_id: int | None = None,
):
    """Newest first. Page with `before=<next_before>` from the previous page."""
    items, next_before = decision_log.read_page(
        limit=limit, before=before, event=event, thread_id=thread_id
    )
    return DecisionsPage(items=items, next_before=next_before)


@router.get("/export.csv", response_class=Response)
async def export_decisions(
    limit: int = Query(default=decision_log.MAX_EXPORT, ge=1, le=decision_log.MAX_EXPORT),
    event: DecisionEvent | None = None,
    thread_id: int | None = None,
):
    """The log as CSV, newest first, with the same filters as the list. Every
    cell is redacted again, and one a spreadsheet would treat as a formula is
    written as plain text."""
    entries = islice(decision_log.iter_entries(event=event, thread_id=thread_id), limit)
    return Response(
        decision_log.to_csv(entries),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="decisions.csv"'},
    )
