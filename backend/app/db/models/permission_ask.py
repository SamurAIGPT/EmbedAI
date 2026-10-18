from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Identity, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime


class PermissionAsk(Base):
    __tablename__ = "permission_ask"

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task.id", name="fk_permission_ask_task_id_task"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    bucket: Mapped[str] = mapped_column(String, nullable=False)
    connector: Mapped[str | None] = mapped_column(String, nullable=True)
    tool: Mapped[str | None] = mapped_column(String, nullable=True)
    # pending -> answered | expired
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    # Set once status leaves "pending": allow | deny
    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    # Whether "allow"/"deny" should also be written as a standing permission_rule.
    always: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
