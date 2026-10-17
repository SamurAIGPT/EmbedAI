"""Persistence for named agents (F06).

Like `thread_store`, every public function is one transaction that takes the
write lock first and commits before it returns. This module knows nothing about
providers or processes: the router checks the provider name and the runtime
applies an agent's settings to a turn.
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, Thread
from app.db.models.agent import APPROVAL_MODES
from app.db.transactions import begin_write

# Fields `update_agent` may change. `archived` is handled separately.
_EDITABLE = (
    "name",
    "title",
    "description",
    "instructions",
    "provider",
    "model",
    "workspace_path",
    "approval_mode",
    "pinned",
    "memory_enabled",
    "recall_enabled",
)


class AgentNotFound(Exception):
    pass


class AgentNameTaken(Exception):
    """Another live agent of this owner already has the name."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _check_mode(mode: str) -> None:
    if mode not in APPROVAL_MODES:
        raise ValueError(f"approval_mode must be one of {', '.join(APPROVAL_MODES)}")


async def get_agent(db: AsyncSession, agent_id: int) -> Agent | None:
    return await db.get(Agent, agent_id)


async def list_agents(
    db: AsyncSession, owner_id: str, *, include_archived: bool = False
) -> list[Agent]:
    query = select(Agent).where(Agent.owner_id == owner_id)
    if not include_archived:
        query = query.where(Agent.archived_at.is_(None))
    # Pinned first, then by name, so a list in a picker is stable.
    query = query.order_by(Agent.pinned.desc(), Agent.name, Agent.id)
    return list((await db.execute(query)).scalars())


async def create_agent(
    db: AsyncSession,
    *,
    owner_id: str,
    name: str,
    title: str | None = None,
    description: str | None = None,
    instructions: str = "",
    provider: str = "claude",
    model: str | None = None,
    workspace_path: str | None = None,
    approval_mode: str = "ask",
    pinned: bool = False,
    memory_enabled: bool = True,
    recall_enabled: bool = True,
) -> Agent:
    """Raises ValueError (blank name, unknown approval mode) or AgentNameTaken."""
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    _check_mode(approval_mode)
    await begin_write(db)
    now = _utcnow()
    agent = Agent(
        owner_id=owner_id,
        name=name,
        title=title,
        description=description,
        instructions=instructions,
        provider=provider,
        model=model,
        workspace_path=workspace_path,
        approval_mode=approval_mode,
        pinned=pinned,
        memory_enabled=memory_enabled,
        recall_enabled=recall_enabled,
        created_at=now,
        updated_at=now,
    )
    db.add(agent)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AgentNameTaken(name) from None
    return agent


async def update_agent(
    db: AsyncSession, agent_id: int, *, archived: bool | None = None, **changes
) -> Agent:
    """Change the given fields. Raises AgentNotFound, ValueError, or
    AgentNameTaken (also when un-archiving into a name now in use)."""
    unknown = set(changes) - set(_EDITABLE)
    if unknown:
        raise ValueError(f"cannot change: {', '.join(sorted(unknown))}")
    if "approval_mode" in changes:
        _check_mode(changes["approval_mode"])
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if not changes["name"]:
            raise ValueError("name is required")
    await begin_write(db)
    agent = await db.get(Agent, agent_id, with_for_update=True)
    if agent is None:
        await db.rollback()
        raise AgentNotFound(agent_id)
    for field, value in changes.items():
        setattr(agent, field, value)
    if archived is not None:
        agent.archived_at = _utcnow() if archived else None
    agent.updated_at = _utcnow()
    name = agent.name  # read now: after a rollback the row is expired
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AgentNameTaken(name) from None
    await db.refresh(agent)
    return agent


async def free_name(db: AsyncSession, owner_id: str, base: str) -> str:
    """`base` when no live agent of the owner has it, else "<base> 2", "<base> 3"...
    A concurrent writer can still take it; that surfaces as AgentNameTaken."""
    live = set(
        (
            await db.execute(
                select(Agent.name).where(Agent.owner_id == owner_id, Agent.archived_at.is_(None))
            )
        ).scalars()
    )
    name, number = base, 1
    while name in live:
        number += 1
        name = f"{base} {number}"
    return name


async def duplicate_agent(db: AsyncSession, agent_id: int) -> Agent:
    """A copy with the same settings, named "<name> copy" (then "copy 2", ...).
    The copy is live and unpinned, and starts with no notes of its own.
    Raises AgentNotFound."""
    source = await db.get(Agent, agent_id)
    if source is None:
        raise AgentNotFound(agent_id)
    name = await free_name(db, source.owner_id, f"{source.name} copy")
    return await create_agent(
        db,
        owner_id=source.owner_id,
        name=name,
        title=source.title,
        description=source.description,
        instructions=source.instructions,
        provider=source.provider,
        model=source.model,
        workspace_path=source.workspace_path,
        approval_mode=source.approval_mode,
        memory_enabled=source.memory_enabled,
        recall_enabled=source.recall_enabled,
    )


async def delete_agent(db: AsyncSession, agent_id: int) -> None:
    """Remove the agent. Its threads keep their whole conversation and simply
    stop being linked: later turns use the thread's own provider and model.
    Raises AgentNotFound."""
    await begin_write(db)
    agent = await db.get(Agent, agent_id, with_for_update=True)
    if agent is None:
        await db.rollback()
        raise AgentNotFound(agent_id)
    await db.execute(
        update(Thread)
        .where(Thread.agent_id == agent_id)
        .values(agent_id=None, updated_at=Thread.updated_at)
        .execution_options(synchronize_session=False)
    )
    await db.delete(agent)
    await db.commit()
