from datetime import datetime

from sqlalchemy import BigInteger, Identity, Index, String, text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, JSONValue, UTCDateTime

# `new`: the provider has not created its session yet. `active`: it has.
# `broken`: a resume failed and the person must reset the session.
# `none`: this provider keeps no session; the app owns the history.
# Closed sets are checked in the repository, not by the database, so that
# changing one never needs a table rebuild on SQLite.
SESSION_STATES = ("new", "active", "broken", "none")


class Thread(Base):
    """A durable conversation. Provider-neutral: nothing here is specific to
    Claude. `provider` and `model` are free text (an adapter registry decides
    which values are valid); adapter-private data lives in `provider_state`."""

    __tablename__ = "thread"
    __table_args__ = (
        Index("ix_thread_owner_listing", "owner_id", "archived_at", "updated_at"),
        # Two threads can never share one provider session.
        Index(
            "uq_thread_provider_session",
            "provider",
            "provider_session_id",
            unique=True,
            sqlite_where=text("provider_session_id IS NOT NULL"),
            postgresql_where=text("provider_session_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    # What the person asked for; null means the provider's default.
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    session_state: Mapped[str] = mapped_column(
        String, nullable=False, default="new", server_default="new"
    )
    # Small, adapter-private state. Never secrets, endpoints or keys.
    provider_state: Mapped[dict | None] = mapped_column(JSONValue, nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # The named agent this thread was created from, if any. Not a database
    # foreign key: deleting an agent never deletes or rewrites its threads.
    agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Last event sequence number handed out in this thread.
    last_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
