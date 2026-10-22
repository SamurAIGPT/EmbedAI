from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Identity, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.base import Base
from app.db.types import BigIntPK, JSONValue, UTCDateTime

# Checked in the service, not the database, so adding one never needs a table
# rebuild on SQLite.
ROUTINE_RUN_STATES = (
    "queued", "running", "needs_input", "completed", "failed", "missed", "skipped", "interrupted",
)
ACTIVE_RUN_STATES = ("queued", "running", "needs_input")
# "retry" is a new attempt of a failed run (Routine.retry_limit).
ROUTINE_TRIGGERS = ("schedule", "manual", "webhook", "retry")


class Routine(Base):
    """A prompt that one local agent runs on a schedule, by webhook, or by
    hand (docs/ROUTINES.md). Every run is a new conversation of that agent."""

    __tablename__ = "routine"
    __table_args__ = (
        # Serves the scheduler's "what is due" query.
        Index("ix_routine_paused_next_run_at", "paused", "next_run_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    # No foreign key: deleting an agent must not be blocked by its routines; a
    # routine whose agent is gone fails its next run and is paused.
    agent_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_kind: Mapped[str] = mapped_column(String, nullable=False)
    schedule_spec: Mapped[dict] = mapped_column(JSONValue, nullable=False, default=dict)
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC", server_default="UTC")
    paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=expression.false()
    )
    # Why the app paused it (agent gone), not set when a person pauses it.
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    run_limit_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    approval_mode: Mapped[str] = mapped_column(
        String, nullable=False, default="ask", server_default="ask"
    )
    approval_timeout_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    catch_up_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
    # How many times a failed run is tried again (0 = never), and how long
    # after the failure each new attempt starts.
    retry_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retry_delay_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    # SHA-256 hex of the webhook secret; null means the webhook is off. The
    # secret itself is never stored.
    webhook_secret_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RoutineRun(Base):
    """One run of a routine, or a record of an occurrence that did not run
    (missed, skipped)."""

    __tablename__ = "routine_run"
    __table_args__ = (
        Index("ix_routine_run_routine_id_created_at", "routine_id", "created_at"),
        Index("ix_routine_run_state", "state"),
        # A scheduled occurrence is recorded at most once, so two schedulers
        # (or a retry after a crash) cannot both start it.
        Index(
            "uq_routine_run_occurrence",
            "routine_id",
            "scheduled_for",
            unique=True,
            sqlite_where=text("trigger = 'schedule'"),
            postgresql_where=text("trigger = 'schedule'"),
        ),
        # A failed run gets at most one new attempt, so two schedulers that
        # both settle it cannot both queue a retry.
        Index("uq_routine_run_retry_of", "retry_of", unique=True),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    routine_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("routine.id", name="fk_routine_run_routine_id_routine"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="queued", server_default="queued")
    scheduled_for: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # The exact text sent to the agent (a webhook run includes its payload).
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The conversation and turn the run used. No foreign keys: deleting a
    # conversation keeps the run history.
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    turn_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # While needs_input: since when. waited_seconds totals earlier waits, which
    # the run limit does not count.
    waiting_since: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    waited_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 1 for a first try; a retry has the failed run's attempt + 1 and its id.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    retry_of: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
