from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Identity,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime


class Tasks(Base):
    __tablename__ = "task"
    # One task per scheduled occurrence. NULLs are distinct, so manual tasks
    # (no schedule) are unaffected.
    __table_args__ = (
        UniqueConstraint(
            "schedule_id", "scheduled_for", name="uq_task_schedule_id_scheduled_for"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigIntPK, Identity(always=True), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    box_id: Mapped[str] = mapped_column(String, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    prompt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Claude conversation id (ours, passed via --session-id / --resume) so a
    # follow-up task can continue this exact conversation instead of
    # starting a fresh one. Null for tasks created before this existed.
    session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    schedule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("schedule.id", name="fk_task_schedule_id_schedule"), nullable=True, index=True
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set once schedule_service.report_finished_runs has notified the user
    # about this run (only set for scheduled tasks; manual tasks stay NULL).
    reported_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
