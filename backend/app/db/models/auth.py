"""Owner authentication and paired devices (docs/AUTH.md).

Secrets are never stored: the owner passphrase as a slow KDF hash, session
and device tokens and pairing codes as SHA-256 digests of high-entropy values.
"""

from datetime import datetime

from sqlalchemy import Identity, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import BigIntPK, UTCDateTime

SESSION_KINDS = ("browser", "device")
SCOPES = ("admin", "chat")


class OwnerAccount(Base):
    """The single owner of a self-hosted install. At most one row.

    `password_hash` is empty until a passphrase is set (the desktop app never
    needs one). `user_id` is the web identity the owner's browser acts as
    (a Telegram id or a `web_` guest id); it lives here, not in the browser.
    """

    __tablename__ = "auth_owner"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )


class AuthSession(Base):
    """A signed-in browser (cookie only) or a paired device (bearer only)."""

    __tablename__ = "auth_session"
    __table_args__ = (
        Index("uq_auth_session_token_hash", "token_hash", unique=True),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Space-separated subset of SCOPES.
    scopes: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class PairingCode(Base):
    """A one-time code the owner opens to pair a device. Single use, short-lived."""

    __tablename__ = "auth_pairing_code"
    __table_args__ = (
        Index("uq_auth_pairing_code_code_hash", "code_hash", unique=True),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(BigIntPK, Identity(always=True), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    scopes: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
