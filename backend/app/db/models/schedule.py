from datetime import datetime

from sqlalchemy import Boolean, Identity, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime


class Schedule(Base):
    __tablename__ = "schedule"

    id: Mapped[int] = mapped_column(
        BigIntPK, Identity(always=True), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    box_id: Mapped[str] = mapped_column(String, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    cron_expression: Mapped[str] = mapped_column(String, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, index=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    # Why the most recent occurrence was skipped (late, or previous run still
    # active). Failures after a run starts are recorded on its Tasks row.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Runs of failed Tasks in a row since the last success; reset on success
    # or resume. Drives the auto-pause in schedule_service.report_finished_runs.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Why is_active was turned off by report_finished_runs (never by the user
    # pausing manually — that's self-explanatory).
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft delete so Tasks.schedule_id keeps pointing at the schedule that
    # produced it.
    deleted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
