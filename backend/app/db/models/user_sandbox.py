from datetime import datetime

from sqlalchemy import Boolean, Identity, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime


class UserSandbox(Base):
    __tablename__ = "user_sandbox"

    id: Mapped[int] = mapped_column(
        BigIntPK, Identity(always=True), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    box_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    machine_type: Mapped[str | None] = mapped_column(String, nullable=True)
    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
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
