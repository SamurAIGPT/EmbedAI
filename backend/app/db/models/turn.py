from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, JSONValue, UTCDateTime

TURN_STATUSES = ("running", "succeeded", "failed", "cancelled", "interrupted")


class Turn(Base):
    """One prompt and the run that answers it. A thread has many turns, one at
    a time: the database enforces that with a unique partial index."""

    __tablename__ = "turn"
    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_turn_thread_id_seq"),
        # At most one running turn per thread.
        Index(
            "uq_turn_one_running_per_thread",
            "thread_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
        # Serves the sweep for stale turns.
        Index("ix_turn_status_heartbeat", "status", "heartbeat_at"),
        # SQLite: never reuse a deleted row's id (links and the decision log name
        # conversations by id). PostgreSQL identity columns never reuse one anyway.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("thread.id", name="fk_turn_thread_id_thread"), nullable=False
    )
    # Per thread, from 1.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="running", server_default="running"
    )
    # Machine-readable cause, so the UI can offer the right action.
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    # The final assistant text, or the partial text of a failed turn.
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the provider reports it used and how much it cost, when it says.
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSONValue, nullable=True)
    # Held only by the run that owns the turn. Every heartbeat and completion
    # must match it.
    owner_token: Mapped[str | None] = mapped_column(String, nullable=True)
    # The backend process that started the turn. Diagnostics only.
    owner_instance: Mapped[str | None] = mapped_column(String, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
