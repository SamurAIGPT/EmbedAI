"""Non-secret application preferences and their security audit trail."""
from datetime import datetime

from sqlalchemy import Identity, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, JSONValue, UTCDateTime


class AppSetting(Base):
    __tablename__ = "app_setting"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[object] = mapped_column(JSONValue, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())
    updated_by: Mapped[str] = mapped_column(String, nullable=False)


class SettingAudit(Base):
    __tablename__ = "setting_audit"
    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    key: Mapped[str] = mapped_column(String, nullable=False)
    old_value_json: Mapped[object] = mapped_column(JSONValue, nullable=True)
    new_value_json: Mapped[object] = mapped_column(JSONValue, nullable=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, server_default=func.now())
    actor: Mapped[str] = mapped_column(String, nullable=False)
