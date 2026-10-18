from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONValue, UTCDateTime

APPROVAL_STATUSES = ("pending", "allowed", "denied", "expired", "cancelled")
# What a call does, in provider-neutral words set by the adapter. An open set:
# the repository does not restrict it.
APPROVAL_KINDS = ("command", "file_write", "file_read", "network", "other")


class Approval(Base):
    """A durable question about one tool call. Valid only while its turn is
    running; every transition is one conditional update (see thread_store)."""

    __tablename__ = "approval"
    __table_args__ = (Index("ix_approval_turn_id_status", "turn_id", "status"),)

    # The random id the UI uses.
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("thread.id", name="fk_approval_thread_id_thread"), nullable=False
    )
    turn_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("turn.id", name="fk_approval_turn_id_turn"), nullable=False
    )
    # The provider's own id for the question.
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    # The provider's own tool name, the input in full, and what the person saw.
    tool: Mapped[str] = mapped_column(String, nullable=False)
    input: Mapped[dict] = mapped_column(JSONValue, nullable=False, default=dict)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str | None] = mapped_column(String, nullable=True)
    # `allowed` records the person's decision; delivered_at records whether it
    # reached the provider.
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
