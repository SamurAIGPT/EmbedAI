import json

from boat_sdk.exceptions import ApiException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.db.models.user_credential import UserCredential
from app.db.transactions import begin_write, release_snapshot
from app.services.box_operations import ascii_box_service


async def _get_row(
    db: AsyncSession, user_id: str, provider: str
) -> UserCredential | None:
    result = await db.execute(
        select(UserCredential).where(
            UserCredential.user_id == user_id,
            UserCredential.provider == provider,
        )
    )
    return result.scalar_one_or_none()


async def save_token(
    db: AsyncSession,
    *,
    user_id: str,
    provider: str,
    file_path: str,
    plaintext: str,
) -> UserCredential:
    ciphertext = crypto.encrypt(plaintext)

    await begin_write(db)  # read-then-write: lock first so two saves cannot interleave
    record = await _get_row(db, user_id, provider)
    if record is None:
        record = UserCredential(
            user_id=user_id,
            provider=provider,
            file_path=file_path,
            ciphertext=ciphertext,
        )
        db.add(record)
    else:
        record.file_path = file_path
        record.ciphertext = ciphertext

    await db.commit()
    await db.refresh(record)
    return record


async def get_decrypted(
    db: AsyncSession, user_id: str, provider: str
) -> tuple[str, str] | None:
    record = await _get_row(db, user_id, provider)
    if record is None:
        return None

    decrypted = crypto.decrypt(record.ciphertext), record.file_path
    # Every caller goes on to talk to a sandbox; do not hold a snapshot meanwhile.
    await release_snapshot(db)
    return decrypted


class CredentialNotFoundError(Exception):
    pass


def _claude_expires_at(content: str) -> int | None:
    try:
        expires_at = json.loads(content)["claudeAiOauth"]["expiresAt"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return expires_at if isinstance(expires_at, int) else None


# Providers whose CLI renews (and rotates) its login in place on the box. For
# these the box copy can be newer than ours and must win over the stored one.
EXPIRY_READERS = {"claude": _claude_expires_at}


async def _read_box_file(box_id: str, file_path: str) -> str | None:
    try:
        response = await run_in_threadpool(ascii_box_service.read_file, box_id, file_path)
    except ApiException:
        return None
    return response.content


async def pull_renewed_credential_from_box(
    db: AsyncSession, *, box_id: str, user_id: str, provider: str
) -> bool:
    """Save the box's copy of the credential if it's newer than the stored one.
    Returns True if the box copy was newer (and is now stored)."""
    read_expiry = EXPIRY_READERS.get(provider)
    if read_expiry is None:
        return False
    decrypted = await get_decrypted(db, user_id, provider)
    if decrypted is None:
        return False

    stored, file_path = decrypted
    on_box = await _read_box_file(box_id, file_path)
    if on_box is None:
        return False

    box_expiry, stored_expiry = read_expiry(on_box), read_expiry(stored)
    if box_expiry is None or (stored_expiry is not None and box_expiry <= stored_expiry):
        return False

    await save_token(
        db, user_id=user_id, provider=provider, file_path=file_path, plaintext=on_box
    )
    return True


async def inject_credential_into_box(
    db: AsyncSession, *, box_id: str, user_id: str, provider: str
) -> None:
    decrypted = await get_decrypted(db, user_id, provider)
    if decrypted is None:
        raise CredentialNotFoundError(
            f"No credential found for user {user_id} and provider {provider}"
        )

    # Overwriting a renewed login with our older copy would hand the CLI a
    # refresh token that renewal already invalidated.
    if await pull_renewed_credential_from_box(
        db, box_id=box_id, user_id=user_id, provider=provider
    ):
        return

    plaintext, file_path = decrypted
    
    await run_in_threadpool(ascii_box_service.write_file, box_id, file_path, plaintext)