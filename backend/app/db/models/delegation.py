"""Results and questions waiting to be handed back to a requester."""
from datetime import datetime
from sqlalchemy import BigInteger, String, Identity, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime


class AgentJobInbox(Base):
    """One settled result or parked question for a requester row. Delivery marks
    `delivered_at` in the same transaction that starts the resume, so each item
    reaches the requester once."""

    __tablename__ = "agent_job_inbox"
    __table_args__ = (
        Index("ix_agent_job_inbox_holder", 'holder_id', 'delivered_at', unique=False),
        Index("ix_agent_job_inbox_job", 'job_id', unique=False),
        {"sqlite_autoincrement": True},
    )
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    holder_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
