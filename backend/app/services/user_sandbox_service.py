from datetime import datetime, timedelta, timezone

from boat_sdk.exceptions import ApiException
from boat_sdk.models.sandbox import Sandbox
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user_sandbox import UserSandbox
from app.db.transactions import begin_write, release_snapshot
from app.services.box_operations import ascii_box_service


async def create(
    db: AsyncSession,
    *,
    user_id: str,
    box_id: str,
    state: str | None,
    machine_type: str | None,
    ttl_seconds: int,
) -> UserSandbox:
    record = UserSandbox(
        user_id=user_id,
        box_id=box_id,
        state=state,
        machine_type=machine_type,
        ttl_seconds=ttl_seconds,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_by_box_id(db: AsyncSession, box_id: str) -> UserSandbox | None:
    result = await db.execute(
        select(UserSandbox).where(UserSandbox.box_id == box_id)
    )
    return result.scalar_one_or_none()


async def get_active_by_user_id(db: AsyncSession, user_id: str) -> UserSandbox | None:
    result = await db.execute(
        select(UserSandbox)
        .where(UserSandbox.user_id == user_id, UserSandbox.is_active.is_(True))
        .order_by(UserSandbox.created_at.desc())
    )
    return result.scalars().first()


async def update_state(
    db: AsyncSession, box_id: str, *, state: str | None
) -> UserSandbox | None:
    await begin_write(db)
    record = await get_by_box_id(db, box_id)
    if record is None:
        return None

    record.state = state
    await db.commit()
    await db.refresh(record)
    return record


async def deactivate(db: AsyncSession, box_id: str) -> UserSandbox | None:
    await begin_write(db)
    record = await get_by_box_id(db, box_id)
    if record is None:
        return None

    record.is_active = False
    await db.commit()
    await db.refresh(record)
    return record


async def ensure(
    db: AsyncSession, *, user_id: str, ttl_seconds: int
) -> tuple[UserSandbox | None, Sandbox]:
    """Reuse an existing sandbox, replacing its stale row only on remote 404."""
    existing = await get_active_by_user_id(db, user_id)
    # The box calls below take seconds; hold no snapshot while they run.
    await release_snapshot(db)
    if existing is not None:
        try:
            response = await run_in_threadpool(
                ascii_box_service.get_box, existing.box_id
            )
        except ApiException as exc:
            if exc.status != 404:
                raise
            existing.is_active = False
        else:
            record = await update_state(
                db, existing.box_id, state=response.sandbox.state
            )
            return record, response.sandbox

    try:
        box = await run_in_threadpool(ascii_box_service.create_box, ttl_seconds)
        # Deactivation and the replacement row commit together. A failed
        # create leaves the old row unchanged so the next request can retry.
        record = await create(
            db,
            user_id=user_id,
            box_id=box.id,
            state=box.state,
            machine_type=box.type,
            ttl_seconds=ttl_seconds,
        )
    except Exception:
        await db.rollback()
        raise
    return record, box
