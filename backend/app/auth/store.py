"""Database side of owner authentication: the owner row, sessions (signed-in
browsers and paired devices) and one-time pairing codes."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import credentials
from app.db.models.auth import SCOPES, AuthSession, OwnerAccount, PairingCode
from app.db.transactions import begin_write

SESSION_TTL = timedelta(days=30)
SESSION_MAX_AGE = timedelta(days=180)
SESSION_RENEW_WHEN_LEFT = timedelta(days=15)
LAST_SEEN_WRITE_INTERVAL = timedelta(seconds=60)

PAIRING_TTL = timedelta(minutes=5)
MAX_OPEN_PAIRING_CODES = 5
# Wrong guesses while codes are open, from anyone: past this every open code
# is dead and the owner makes a new one.
MAX_PAIRING_FAILURES = 10
MAX_ACTIVE_SESSIONS = 100
LABEL_MAX = 80


def utcnow() -> datetime:
    return datetime.now(UTC)


def clean_label(raw: str | None, fallback: str = "Unnamed device") -> str:
    text = "".join(ch if ch.isprintable() else " " for ch in (raw or "")).strip()
    return (text or fallback)[:LABEL_MAX]


def parse_scopes(value: str) -> tuple[str, ...]:
    return tuple(s for s in value.split() if s in SCOPES)


def scopes_for(scope: str) -> str:
    """'admin' includes chat; 'chat' is chat only."""
    return "admin chat" if scope == "admin" else "chat"


# --- owner ------------------------------------------------------------------


async def get_owner(db: AsyncSession) -> OwnerAccount | None:
    result = await db.execute(select(OwnerAccount).order_by(OwnerAccount.id).limit(1))
    return result.scalar_one_or_none()


async def owner_configured(db: AsyncSession) -> bool:
    owner = await get_owner(db)
    return owner is not None and bool(owner.password_hash)


async def _owner_for_write(db: AsyncSession) -> OwnerAccount:
    await begin_write(db)
    owner = await get_owner(db)
    if owner is None:
        owner = OwnerAccount()
        db.add(owner)
        await db.flush()
    return owner


class OwnerAlreadyConfigured(Exception):
    pass


async def set_passphrase(db: AsyncSession, passphrase: str, *, first_time_only: bool) -> None:
    """Hash and store the owner passphrase. Changing an existing one signs out
    every browser (paired devices keep their tokens)."""
    credentials.check_passphrase_strength(passphrase)
    hashed = await asyncio.to_thread(credentials.hash_passphrase, passphrase)
    owner = await _owner_for_write(db)
    if owner.password_hash and first_time_only:
        await db.rollback()
        raise OwnerAlreadyConfigured()
    changing = bool(owner.password_hash)
    owner.password_hash = hashed
    owner.updated_at = utcnow()
    if changing:
        await db.execute(
            update(AuthSession)
            .where(AuthSession.kind == "browser", AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
    await db.commit()


async def verify_owner(db: AsyncSession, passphrase: str) -> bool:
    owner = await get_owner(db)
    stored = owner.password_hash if owner else None
    if not stored:
        return False
    return await asyncio.to_thread(credentials.verify_passphrase, passphrase, stored)


async def get_identity(db: AsyncSession) -> str | None:
    owner = await get_owner(db)
    return owner.user_id if owner else None


async def set_identity(db: AsyncSession, user_id: str | None, *, only_if_unset: bool = False) -> str | None:
    """Returns the identity now in force."""
    owner = await _owner_for_write(db)
    if only_if_unset and owner.user_id:
        await db.commit()
        return owner.user_id
    owner.user_id = user_id
    owner.updated_at = utcnow()
    await db.commit()
    return user_id


# --- sessions ---------------------------------------------------------------


async def create_session(db: AsyncSession, *, kind: str, label: str, scopes: str) -> tuple[str, AuthSession]:
    token = credentials.new_session_token()
    now = utcnow()
    record = AuthSession(
        kind=kind,
        label=clean_label(label),
        token_hash=credentials.digest(token),
        scopes=scopes,
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_TTL,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return token, record


@dataclass(frozen=True)
class SessionView:
    id: int
    kind: str
    label: str
    scopes: tuple[str, ...]
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


def _view(record: AuthSession) -> SessionView:
    return SessionView(
        id=record.id,
        kind=record.kind,
        label=record.label,
        scopes=parse_scopes(record.scopes),
        created_at=record.created_at,
        last_seen_at=record.last_seen_at,
        expires_at=record.expires_at,
    )


async def authenticate(db: AsyncSession, token: str, *, kind: str) -> SessionView | None:
    """The live session for `token`, or None. A browser session is accepted
    only as a cookie and a device session only as a bearer token (`kind`)."""
    if not credentials.looks_like_session_token(token):
        return None
    hashed = credentials.digest(token)
    result = await db.execute(select(AuthSession).where(AuthSession.token_hash == hashed))
    record = result.scalar_one_or_none()
    now = utcnow()
    if (
        record is None
        or not credentials.same_digest(record.token_hash, hashed)
        or record.kind != kind
        or record.revoked_at is not None
        or record.expires_at <= now
    ):
        return None
    changes: dict[str, datetime] = {}
    if now - record.last_seen_at >= LAST_SEEN_WRITE_INTERVAL:
        changes["last_seen_at"] = now
    if record.expires_at - now < SESSION_RENEW_WHEN_LEFT:
        renewed = min(now + SESSION_TTL, record.created_at + SESSION_MAX_AGE)
        if renewed > record.expires_at:
            changes["expires_at"] = renewed
    if changes:
        await begin_write(db)
        await db.execute(
            update(AuthSession)
            .where(AuthSession.id == record.id, AuthSession.revoked_at.is_(None))
            .values(**changes)
        )
        await db.commit()
        for key, value in changes.items():
            setattr(record, key, value)
    return _view(record)


async def list_active_sessions(db: AsyncSession) -> list[SessionView]:
    result = await db.execute(
        select(AuthSession)
        .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow())
        .order_by(AuthSession.created_at.desc(), AuthSession.id.desc())
    )
    return [_view(r) for r in result.scalars()]


async def count_active_sessions(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(AuthSession)
        .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow())
    )
    return int(result.scalar_one())


async def revoke_session(db: AsyncSession, session_id: int) -> bool:
    await begin_write(db)
    result = await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()
    return (result.rowcount or 0) > 0


async def revoke_token(db: AsyncSession, token: str) -> None:
    await begin_write(db)
    await db.execute(
        update(AuthSession)
        .where(AuthSession.token_hash == credentials.digest(token), AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()


# --- pairing codes ----------------------------------------------------------


class TooManyOpenCodes(Exception):
    pass


async def create_pairing_code(db: AsyncSession, *, label: str, scope: str) -> tuple[str, PairingCode]:
    now = utcnow()
    await begin_write(db)
    # Old rows carry nothing secret, but there is no reason to keep them.
    await db.execute(delete(PairingCode).where(PairingCode.expires_at < now - timedelta(days=1)))
    open_count = (await db.execute(
        select(func.count()).select_from(PairingCode).where(
            PairingCode.used_at.is_(None),
            PairingCode.expires_at > now,
            PairingCode.failed_attempts < MAX_PAIRING_FAILURES,
        )
    )).scalar_one()
    if open_count >= MAX_OPEN_PAIRING_CODES:
        await db.rollback()
        raise TooManyOpenCodes()
    code = credentials.new_pairing_code()
    record = PairingCode(
        code_hash=credentials.digest(code),
        label=clean_label(label, fallback=""),
        scopes=scopes_for(scope),
        created_at=now,
        expires_at=now + PAIRING_TTL,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return code, record


async def redeem_pairing_code(db: AsyncSession, raw_code: str) -> PairingCode | None:
    """Consume the code, or count a wrong guess against every open code.
    Single use: of two concurrent redemptions only one gets the row."""
    code = credentials.normalize_pairing_code(raw_code)
    now = utcnow()
    hashed = credentials.digest(code)
    await begin_write(db)
    live = (
        PairingCode.used_at.is_(None),
        PairingCode.expires_at > now,
        PairingCode.failed_attempts < MAX_PAIRING_FAILURES,
    )
    consumed = await db.execute(
        update(PairingCode).where(PairingCode.code_hash == hashed, *live).values(used_at=now)
    )
    if (consumed.rowcount or 0) == 1:
        record = (await db.execute(select(PairingCode).where(PairingCode.code_hash == hashed))).scalar_one()
        await db.commit()
        return record
    await db.execute(
        update(PairingCode).where(*live).values(failed_attempts=PairingCode.failed_attempts + 1)
    )
    await db.commit()
    return None
