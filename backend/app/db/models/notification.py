from datetime import datetime

from sqlalchemy import BigInteger, Identity, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime


class Notification(Base):
    """An entry in the in-app notification feed. Routines write them today;
    `kind` is an open set checked in the service."""

    __tablename__ = "notification"
    __table_args__ = (
        Index("ix_notification_owner_id_read_at", "owner_id", "read_at"),
        Index("ix_notification_owner_id_created_at", "owner_id", "created_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What a click opens. No foreign keys: a notification outlives what it names.
    routine_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
