from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, JSONValue, UTCDateTime


class Event(Base):
    """An ordered, user-visible fact inside a thread. `type` is free text with
    no database constraint, so a new event type needs no migration. The unique
    (thread_id, seq) index is also the replay index."""

    __tablename__ = "event"
    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_event_thread_id_seq"),
        Index("ix_event_turn_id", "turn_id"),
        # SQLite: never reuse a deleted row's id (links and the decision log name
        # conversations by id). PostgreSQL identity columns never reuse one anyway.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("thread.id", name="fk_event_thread_id_thread"), nullable=False
    )
    # Null for thread-level events (for example a session reset).
    turn_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("turn.id", name="fk_event_turn_id_turn"), nullable=True
    )
    # Per thread, from 1, gap-free. Commit order is seq order.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[dict] = mapped_column(JSONValue, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
