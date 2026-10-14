from datetime import datetime

from sqlalchemy import Identity, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime


class UserCredential(Base):
    __tablename__ = "user_credential"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_credential_user_provider"),
    )

    id: Mapped[int] = mapped_column(
        BigIntPK, Identity(always=True), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
