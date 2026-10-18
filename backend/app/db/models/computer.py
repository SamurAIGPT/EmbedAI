from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Identity, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime

# What the app last knew of the machine. `deleting` is written before the
# machine is removed and the row goes only after, so a removal interrupted by
# a crash or a stopped Docker is finished later instead of leaving an orphan.
COMPUTER_STATES = ("missing", "creating", "running", "stopped", "error", "deleting")
COMPUTER_PROVIDERS = ("docker",)
COMPUTER_NETWORKS = ("none", "default")


class Computer(Base):
    """An agent's own isolated machine (docs/COMPUTERS.md). At most one per
    agent. The machine itself lives in the provider (a Docker container); this
    row is its configuration and the last state the app saw."""

    __tablename__ = "computer"
    __table_args__ = (
        Index("uq_computer_agent_id", "agent_id", unique=True),
        # SQLite: never reuse a removed computer's id; the container name is
        # derived from it. PostgreSQL identity columns never reuse one anyway.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    # Like thread.agent_id, no foreign key: removing an agent marks its
    # computer `deleting` first, and the row outlives it until the machine is gone.
    agent_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str] = mapped_column(
        String, nullable=False, default="docker", server_default="docker"
    )
    image: Mapped[str] = mapped_column(String, nullable=False)
    # The image's content id when the machine was made, so a moved tag is noticed.
    image_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Thousandths of a CPU, so the column is an integer on every database.
    cpu_millis: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1000, server_default="1000"
    )
    memory_mb: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1024, server_default="1024"
    )
    pids_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=256, server_default="256"
    )
    network: Mapped[str] = mapped_column(
        String, nullable=False, default="none", server_default="none"
    )
    # Stopped after this many minutes without a turn or a command; 0 never.
    idle_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    # A turn may start a stopped machine (or make a missing one from an image
    # already on this computer). Off: the person starts it.
    auto_start: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=expression.true()
    )
    state: Mapped[str] = mapped_column(
        String, nullable=False, default="missing", server_default="missing"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The folder mounted at /workspace when the machine was made.
    workspace_path: Mapped[str | None] = mapped_column(String, nullable=True)
    container_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
