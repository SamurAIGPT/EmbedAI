from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, String, Text, text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime
from sqlalchemy import Identity

# How much an agent may do before asking. Keep in step with the mode names the
# Claude runner understands.
# Checked in the service, not the database, so adding a mode never needs a
# table rebuild on SQLite.
APPROVAL_MODES = ("ask", "accept_edits", "plan")


class Agent(Base):
    """A named, reusable agent: instructions, engine/model, workspace and
    approval mode. A thread created from an agent keeps the agent's id, and
    each turn reads the agent's current settings."""

    __tablename__ = "agent"
    __table_args__ = (
        # One live agent per name and owner; archived agents free their name.
        Index(
            "uq_agent_owner_name_live",
            "owner_id",
            "name",
            unique=True,
            sqlite_where=text("archived_at IS NULL"),
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # A short role line ("Release manager") and a longer description, shown in
    # lists and pickers; neither is sent to the provider.
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    provider: Mapped[str] = mapped_column(
        String, nullable=False, default="claude", server_default="claude"
    )
    # Null means the provider's default model.
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    # Another engine (and its model, null for its default) a turn moves to when
    # this agent's engine refuses it for a usage or rate limit before starting
    # on it, if that engine can do what the turn needs (docs/ENGINES.md).
    fallback_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    fallback_model: Mapped[str | None] = mapped_column(String, nullable=True)
    # Null means the default workspace root, resolved when a thread is created.
    workspace_path: Mapped[str | None] = mapped_column(String, nullable=True)
    approval_mode: Mapped[str] = mapped_column(
        String, nullable=False, default="ask", server_default="ask"
    )
    # Off: the agent's notes (MEMORY.md in its own folder) are neither loaded
    # into its conversations nor mentioned to it. The file itself stays.
    memory_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=expression.true()
    )
    # Off: no notes or earlier conversations are looked up and added to the
    # person's message before a turn (docs/MEMORY.md). Needs memory_enabled.
    recall_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=expression.true()
    )
    # Off: no model call files facts noticed in this agent's conversations
    # (docs/MEMORY.md, "Noticing facts"). Needs memory_enabled.
    memory_upkeep: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=expression.true()
    )
    # Pinned agents list first.
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=expression.false()
    )
    team_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delegation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=expression.true())
    created_by_agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    instructions_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
