"""Owner-scoped planning CRUD with compare-and-swap revisions. No execution hooks."""
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import Agent, Team, Thread, WorkBoard, WorkColumn, WorkItem
from app.db.transactions import begin_write
from app.schemas.work_item import WorkFields

COLUMNS = [("backlog", "Backlog"), ("ready", "Ready"), ("doing", "In progress"),
           ("waiting", "Waiting"), ("review", "Review"), ("done", "Done")]


def error(status, message):
    return HTTPException(status_code=status, detail=message)


async def default_board(db, owner):
    await begin_write(db)
    query = select(WorkBoard).where(WorkBoard.owner_id == owner, WorkBoard.key == "default")
    board = await db.scalar(query)
    if board is None:
        try:
            async with db.begin_nested():
                board = WorkBoard(owner_id=owner, key="default", name="My tasks")
                db.add(board)
                await db.flush()
                db.add_all([WorkColumn(board_id=board.id, key=key, name=name, position=i)
                            for i, (key, name) in enumerate(COLUMNS)])
                await db.flush()
        except IntegrityError:
            # Another tab initialized this owner's board first.
            board = await db.scalar(query)
            if board is None:
                raise
    await db.commit()
    return board


async def owned_board(db, owner, board_id):
    board = await db.scalar(select(WorkBoard).where(WorkBoard.id == board_id, WorkBoard.owner_id == owner))
    if board is None:
        raise error(404, "Board not found")
    return board


async def column_for(db, board_id, column_id):
    column = await db.scalar(select(WorkColumn).where(WorkColumn.id == column_id, WorkColumn.board_id == board_id))
    if column is None:
        raise error(422, "Choose a column from this board")
    return column


async def validate_fields(db, owner, data, changed):
    try:
        WorkFields.model_validate({key: value for key, value in data.items() if key in WorkFields.model_fields})
    except ValidationError as exc:
        raise error(422, str(exc)) from None
    if data.get("due_at") and data.get("due_date"):
        raise error(422, "Choose a date-only deadline or a timed deadline")
    if data.get("agent_id") and data.get("team_id"):
        raise error(422, "Assign a task to an agent or a team")
    for field, model in (("agent_id", Agent), ("team_id", Team), ("thread_id", Thread)):
        value = data.get(field)
        if field in changed and value is not None:
            found = await db.scalar(select(model.id).where(model.id == value, model.owner_id == owner))
            if found is None:
                raise error(422, f"The selected {field.removesuffix('_id')} is unavailable")


async def next_position(db, board_id, column_id):
    return (await db.scalar(select(func.max(WorkItem.position)).where(
        WorkItem.board_id == board_id, WorkItem.column_id == column_id)) or 0) + 1


async def create_item(db, owner, data):
    await begin_write(db)
    await owned_board(db, owner, data["board_id"])
    column = await column_for(db, data["board_id"], data["column_id"])
    await validate_fields(db, owner, data, data)
    now = datetime.now(UTC)
    item = WorkItem(**data, owner_id=owner, position=await next_position(db, data["board_id"], column.id),
                    completed_at=now if column.key == "done" else None)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def update_item(db, owner, item_id, revision, changes):
    await begin_write(db)
    item = await db.scalar(select(WorkItem).where(WorkItem.id == item_id, WorkItem.owner_id == owner))
    if item is None:
        raise error(404, "Task not found")
    if item.revision != revision:
        raise error(409, "This task changed in another window. Refresh it before saving again.")
    if any(changes.get(key, True) is None for key in ("title", "description", "priority", "timezone", "column_id", "archived")):
        raise error(422, "Required task fields cannot be cleared")
    fields = {key: getattr(item, key) for key in WorkFields.model_fields}
    fields.update({key: value for key, value in changes.items() if key in fields})
    # Forms may submit all visible fields. An unchanged, now-deleted link is
    # retained; only choosing a different target requires it to exist today.
    changed_fields = {key for key, value in changes.items() if value != getattr(item, key)}
    await validate_fields(db, owner, fields, changed_fields)
    if "title" in changes:
        changes["title"] = fields["title"].strip()
    if "column_id" in changes and changes["column_id"] != item.column_id:
        column = await column_for(db, item.board_id, changes["column_id"])
        changes["position"] = await next_position(db, item.board_id, column.id)
        changes["completed_at"] = datetime.now(UTC) if column.key == "done" else None
    result = await db.execute(update(WorkItem).where(
        WorkItem.id == item_id, WorkItem.owner_id == owner, WorkItem.revision == revision
    ).values(**changes, revision=revision + 1, updated_at=datetime.now(UTC)).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await db.rollback()
        raise error(409, "This task changed in another window. Refresh it before saving again.")
    await db.commit()
    await db.refresh(item)
    return item
