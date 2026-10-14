import asyncio
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.permission_ask import PermissionAsk
from app.db.models.permission_rule import PermissionRule
from app.db.models.task import Tasks
from app.db.transactions import begin_write

AskDecision = Literal["allow", "deny"]


async def create_ask(
    db: AsyncSession,
    user_id: str,
    bucket: str,
    task_id: int,
    connector: str | None = None,
    tool: str | None = None,
) -> PermissionAsk:
    """
    Record that a task is blocked waiting on a human decision, and mark the
    task as such. Called the moment check_permission returns "ask" — this
    function only records the question, it does not notify anyone.

    Args:
        db (AsyncSession): The database session to use for the query.
        user_id (str): The ID of the user who needs to answer.
        bucket (str): The risk bucket the pending action falls into.
        task_id (int): The task that is blocked on this decision.
        connector (str | None, optional): The connector involved, if any.
        tool (str | None, optional): The tool involved, if any.

    Returns:
        PermissionAsk: The newly created, still-pending ask row.
    """
    await begin_write(db)
    ask = PermissionAsk(
        task_id=task_id,
        user_id=user_id,
        bucket=bucket,
        connector=connector,
        tool=tool,
        status="pending",
    )
    db.add(ask)

    # A plain UPDATE (no read first), so it cannot lose a race with another
    # writer; it does nothing when the task row does not exist.
    await db.execute(
        update(Tasks).where(Tasks.id == task_id).values(status="waiting_approval")
    )

    await db.commit()
    await db.refresh(ask)
    return ask


async def _reload_ask(db: AsyncSession, ask_id: int) -> PermissionAsk | None:
    result = await db.execute(
        select(PermissionAsk)
        .where(PermissionAsk.id == ask_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _resume_task(db: AsyncSession, task_id: int) -> None:
    """Take a task out of waiting_approval once its ask is resolved, however
    it was resolved (answered or expired)."""
    await db.execute(
        update(Tasks)
        .where(Tasks.id == task_id, Tasks.status == "waiting_approval")
        .values(status="running")
    )


async def get_pending_ask_for_task(db: AsyncSession, task_id: int) -> PermissionAsk | None:
    """The ask a task is currently waiting on, if any — lets a caller (the
    web UI) show what's being asked without needing the ask's own id."""
    result = await db.execute(
        select(PermissionAsk)
        .where(PermissionAsk.task_id == task_id, PermissionAsk.status == "pending")
        .order_by(PermissionAsk.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def answer_ask(
    db: AsyncSession,
    ask_id: int,
    decision: AskDecision,
    always: bool = False,
) -> PermissionAsk:
    """
    Resolve a pending ask with a human's decision. Called from either the
    web answer endpoint or the Telegram callback handler — both converge
    here. If `always` is set, also writes a standing permission_rule so the
    same action isn't asked again.

    Args:
        db (AsyncSession): The database session to use for the query.
        ask_id (int): The ask being answered.
        decision ("allow" | "deny"): The human's decision.
        always (bool, optional): Whether to persist this as a standing rule.

    Returns:
        PermissionAsk: The now-answered ask row.
    """
    # Claim the answer with one conditional UPDATE: of two answers arriving at
    # once (web and Telegram), exactly one wins, on every database.
    await begin_write(db)
    claimed = await db.execute(
        update(PermissionAsk)
        .where(PermissionAsk.id == ask_id, PermissionAsk.status == "pending")
        .values(
            status="answered",
            decision=decision,
            always=always,
            answered_at=datetime.now(UTC),
        )
        .execution_options(synchronize_session=False)
    )
    ask = await _reload_ask(db, ask_id)
    if ask is None:
        raise ValueError(f"PermissionAsk {ask_id} not found")
    if claimed.rowcount != 1:
        await db.commit()  # already resolved; nothing to write, release the transaction
        return ask

    if always:
        # Written at the same specificity the ask was captured at. This only
        # matches future lookups because the hook always derives the same
        # (bucket, connector, tool) for the same action — e.g. every "git
        # push" is classified identically, so a rule scoped to
        # connector="github", tool="git.push" matches every future git push,
        # without also silently allowing unrelated github actions.
        # ask.connector/ask.tool are None here when no classifier recognizes
        # the action, so this still degrades to a bucket-level rule for
        # anything coarse (plain Bash, Write, Edit, ...).
        db.add(
            PermissionRule(
                user_id=ask.user_id,
                bucket=ask.bucket,
                connector=ask.connector,
                tool=ask.tool,
                decision=decision,
            )
        )

    await _resume_task(db, ask.task_id)

    await db.commit()
    await db.refresh(ask)
    return ask


async def poll_for_answer(
    db: AsyncSession,
    ask_id: int,
    *,
    timeout_seconds: float,
    interval_seconds: float = 1.0,
) -> AskDecision:
    """
    Block until `ask_id` is answered or the timeout elapses, re-reading the
    row from the database each interval (it is answered by a different
    request, possibly a different process, so the DB is the only shared
    state). An unanswered ask expires and defaults to "deny" — no rule, no
    answer, never means proceed.

    Args:
        db (AsyncSession): The database session to use for the query.
        ask_id (int): The ask to wait on.
        timeout_seconds (float): How long to wait before giving up.
        interval_seconds (float, optional): How often to re-check.

    Returns:
        "allow" | "deny": The resolved decision.
    """
    elapsed = 0.0
    while elapsed < timeout_seconds:
        ask = await _reload_ask(db, ask_id)
        status = ask.status if ask is not None else None
        decision = ask.decision if ask is not None else None
        # End the read transaction before waiting. Otherwise it would hold an
        # old snapshot (on SQLite the next read would not see the answer and a
        # later write would fail) and a connection for up to the whole timeout.
        await db.commit()
        if ask is None or status == "expired":
            return "deny"
        if status == "answered":
            return cast(AskDecision, decision)

        await asyncio.sleep(interval_seconds)
        elapsed += interval_seconds

    # Out of time. Expire the ask only if it is still pending: an answer that
    # landed in the meantime wins (one conditional UPDATE, no read-then-write).
    await begin_write(db)
    expired = await db.execute(
        update(PermissionAsk)
        .where(PermissionAsk.id == ask_id, PermissionAsk.status == "pending")
        .values(status="expired")
        .execution_options(synchronize_session=False)
    )
    ask = await _reload_ask(db, ask_id)
    if expired.rowcount == 1 and ask is not None:
        await _resume_task(db, ask.task_id)
    answered = ask is not None and ask.status == "answered"
    decision = ask.decision if ask is not None else None
    await db.commit()
    if answered:
        return cast(AskDecision, decision)
    return "deny"
