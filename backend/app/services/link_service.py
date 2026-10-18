import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.link_code import LinkCode
from app.db.transactions import begin_write

CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_LENGTH = 6
CODE_TTL_SECONDS = 600


def _generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


async def create_code(db: AsyncSession) -> LinkCode:
    record = LinkCode(
        code=_generate_code(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL_SECONDS),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_code(db: AsyncSession, code: str) -> LinkCode | None:
    result = await db.execute(select(LinkCode).where(LinkCode.code == code))
    return result.scalar_one_or_none()


class LinkCodeInvalidError(Exception):
    pass


class LinkCodeExpiredError(Exception):
    pass


async def claim_code(db: AsyncSession, code: str, user_id: str) -> LinkCode:
    await begin_write(db)  # check-then-claim: two claimers must not both pass the check
    record = await get_code(db, code.strip().upper())
    if record is None:
        raise LinkCodeInvalidError(f"Unknown link code: {code}")

    if record.claimed_at is not None:
        # Already claimed — idempotent if it's the same user reusing it.
        if record.user_id == user_id:
            return record
        raise LinkCodeInvalidError(f"Link code already used: {code}")

    if record.expires_at < datetime.now(timezone.utc):
        raise LinkCodeExpiredError(f"Link code expired: {code}")

    record.user_id = user_id
    record.claimed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(record)
    return record
