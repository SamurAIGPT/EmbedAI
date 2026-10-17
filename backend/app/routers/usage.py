"""Usage and cost, as engines reported them (docs/EVERYDAY.md). Admin scope."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.routers.threads import get_runtime
from app.services import usage_report
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(tags=["Usage"], dependencies=[Depends(require_local_access)])

_MONTH = Query(None, pattern=r"^\d{4}-\d{2}$")


def _bad_month() -> HTTPException:
    return HTTPException(
        status_code=422, detail={"error_code": "bad_month", "message": "month must look like 2026-10."}
    )


@router.get("/usage")
async def usage_summary(month: str | None = _MONTH, rt: ThreadRuntime = Depends(get_runtime)):
    """Totals for one month (default: this month, UTC): overall, by
    conversation, by engine and model, and by day. `cost_usd` is null when no
    turn in that group reported a cost."""
    try:
        async with rt.session() as db:
            return await usage_report.month_summary(db, rt.owner_id, month)
    except ValueError:
        raise _bad_month() from None


@router.get("/usage.csv")
async def usage_csv(month: str | None = _MONTH, rt: ThreadRuntime = Depends(get_runtime)):
    """One row per finished turn of the month."""
    try:
        async with rt.session() as db:
            label, body = await usage_report.month_csv(db, rt.owner_id, month)
    except ValueError:
        raise _bad_month() from None
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="open-dots-usage-{label}.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )
