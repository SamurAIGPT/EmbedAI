"""Tasks and a read-only routine calendar projection using the existing scheduler."""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime
from sqlalchemy import func, select

from app.db.models import Routine, RoutineRun, WorkColumn, WorkItem
from app.routers.threads import get_runtime
from app.schemas.work_item import BoardResponse, ColumnResponse, WorkCreate, WorkResponse, WorkUpdate
from app.services import routine_schedule, work_planner as service
from app.services.local_access_policy import require_local_access

router = APIRouter(prefix="/planner", tags=["Planner"], dependencies=[Depends(require_local_access)])
MAX_PROJECTED_EVENTS = 5000


@router.get("/board", response_model=BoardResponse)
async def board(rt=Depends(get_runtime)):
    async with rt.session() as db:
        board = await service.default_board(db, rt.owner_id)
        columns = (await db.scalars(select(WorkColumn).where(WorkColumn.board_id == board.id)
                                   .order_by(WorkColumn.position))).all()
        return BoardResponse(id=board.id, name=board.name,
                             columns=[ColumnResponse.model_validate(c) for c in columns])


@router.get("/items", response_model=list[WorkResponse])
async def items(board_id: int, archived: bool = False, rt=Depends(get_runtime)):
    async with rt.session() as db:
        await service.owned_board(db, rt.owner_id, board_id)
        return (await db.scalars(select(WorkItem).where(
            WorkItem.owner_id == rt.owner_id, WorkItem.board_id == board_id, WorkItem.archived == archived
        ).order_by(WorkItem.position, WorkItem.id))).all()


@router.post("/items", response_model=WorkResponse, status_code=201)
async def create_item(payload: WorkCreate, rt=Depends(get_runtime)):
    async with rt.session() as db:
        return await service.create_item(db, rt.owner_id, payload.model_dump())


@router.get("/items/{item_id}", response_model=WorkResponse)
async def item(item_id: int, rt=Depends(get_runtime)):
    # Fetch archived cards too, so a stale editor can load the latest record
    # after another window archives it out of the active board.
    async with rt.session() as db:
        record = await db.scalar(select(WorkItem).where(
            WorkItem.id == item_id, WorkItem.owner_id == rt.owner_id))
        if record is None:
            raise service.error(404, "Task not found")
        return record


@router.patch("/items/{item_id}", response_model=WorkResponse)
async def update_item(item_id: int, payload: WorkUpdate, rt=Depends(get_runtime)):
    changes = payload.model_dump(exclude_unset=True, exclude={"revision"})
    async with rt.session() as db:
        return await service.update_item(db, rt.owner_id, item_id, payload.revision, changes)


@router.get("/routine-calendar")
async def routine_calendar(start: AwareDatetime, end: AwareDatetime, rt=Depends(get_runtime)):
    if end <= start or end - start > timedelta(days=62):
        raise service.error(422, "Choose a calendar window of at most 62 days")
    events, warnings = [], []
    now = datetime.now(UTC)
    async with rt.session() as db:
        routines = (await db.scalars(select(Routine).where(Routine.owner_id == rt.owner_id).order_by(Routine.id))).all()
        for routine in routines:
            if len(events) >= MAX_PROJECTED_EVENTS:
                warnings.append(f"Showing the first {MAX_PROJECTED_EVENTS:,} scheduled occurrences. Choose a shorter window to see more.")
                break
            if routine.paused or routine.schedule_kind == "manual":
                continue
            cursor = max(start, now) - timedelta(microseconds=1)
            try:
                capacity = min(500, MAX_PROJECTED_EVENTS - len(events))
                for _ in range(capacity):
                    at = routine_schedule.next_after(routine.schedule_kind, routine.schedule_spec, routine.timezone, cursor)
                    if at is None or at >= end:
                        break
                    events.append(dict(id=f"routine-{routine.id}-{at.isoformat()}", kind="routine",
                                       routine_id=routine.id, title=routine.name, at=at,
                                       state="scheduled", thread_id=None))
                    cursor = at
                else:
                    if capacity == 500:
                        warnings.append(f"{routine.name}: showing the first 500 occurrences in this window.")
                    else:
                        warnings.append(f"Showing the first {MAX_PROJECTED_EVENTS:,} scheduled occurrences. Choose a shorter window to see more.")
                        break
            except (routine_schedule.InvalidSchedule, KeyError, ValueError):
                warnings.append(f"{routine.name}: the schedule could not be read.")
        at_column = func.coalesce(RoutineRun.scheduled_for, RoutineRun.created_at)
        runs = (await db.execute(select(RoutineRun, Routine.name).join(Routine, Routine.id == RoutineRun.routine_id)
            .where(Routine.owner_id == rt.owner_id, at_column >= start, at_column < end)
            .order_by(at_column).limit(2001))).all()
        if len(runs) > 2000:
            warnings.append("Showing the first 2,000 routine runs in this window.")
        # A run receipt supersedes a projection for the same occurrence.
        receipts = {(run.routine_id, run.scheduled_for) for run, _ in runs[:2000]}
        events = [e for e in events if (e["routine_id"], e["at"]) not in receipts]
        for run, name in runs[:2000]:
            events.append(dict(id=f"run-{run.id}", kind="run", routine_id=run.routine_id, title=name,
                               at=run.scheduled_for or run.created_at, state=run.state, thread_id=run.thread_id))
    return {"events": sorted(events, key=lambda e: e["at"]), "warnings": warnings}
