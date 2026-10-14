from datetime import datetime

from sqlalchemy import Identity, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime


class LinkCode(Base):
    __tablename__ = "link_code"

    id: Mapped[int] = mapped_column(
        BigIntPK, Identity(always=True), primary_key=True
    )
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
