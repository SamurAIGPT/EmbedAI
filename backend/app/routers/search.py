"""Search across conversations (docs/EVERYDAY.md)."""

from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.routers.threads import get_runtime
from app.services import conversation_search
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(prefix="/search", tags=["Search"], dependencies=[Depends(require_local_access)])


class SearchHitResponse(BaseModel):
    thread_id: int
    thread_title: str | None
    agent_id: int | None
    archived: bool
    # title | user | assistant
    kind: str
    at: datetime
    snippet: str
    # [start, length] pairs into `snippet`, one per matched word occurrence.
    highlights: list[list[int]]
    seq: int | None
    turn_id: int | None
    room_id: int | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitResponse]


def _day(value: str | None, end: bool) -> datetime | None:
    if not value:
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "bad_date", "message": "Dates must look like 2026-10-17."},
        ) from None
    return datetime.combine(day, time.max if end else time.min, tzinfo=UTC)


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query("", max_length=200),
    agent_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    include_archived: bool = False,
    include_delegated: bool = False,
    limit: int = Query(40, ge=1, le=100),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """Conversations whose title or messages contain every word of `q`, newest
    first, with a highlighted snippet. Under 2 characters returns no hits."""
    start, end = _day(since, False), _day(until, True)
    async with rt.session() as db:
        hits = await conversation_search.search(
            db,
            rt.owner_id,
            q,
            agent_id=agent_id,
            since=start,
            until=end,
            include_archived=include_archived,
            include_delegated=include_delegated,
            limit=limit,
        )
    return SearchResponse(query=q, hits=[SearchHitResponse(**vars(hit)) for hit in hits])
