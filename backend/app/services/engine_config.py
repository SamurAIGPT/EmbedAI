"""Saved engine configuration: an ACP agent's command, an OpenAI-compatible
endpoint and its key.

Stored as one encrypted row per engine in the existing credential table
(`user_credential`, provider `engine:<id>`), through the same encryption as
every other credential, so no migration is needed and a key never sits in
plain text in the database. What the API returns about a configuration comes
from the adapter's `public_config`, which leaves secrets out.
"""

import json

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user_credential import UserCredential
from app.db.transactions import begin_write
from app.services import user_credential_service

PREFIX = "engine:"
# The credential table needs a path; engine configurations are not files.
_NOT_A_FILE = "-"


def _provider(engine_id: str) -> str:
    return PREFIX + engine_id


async def load(db: AsyncSession, owner_id: str, engine_id: str) -> dict | None:
    """The saved configuration, or None when there is none or it cannot be
    read (a key that no longer decrypts reads as no configuration)."""
    try:
        found = await user_credential_service.get_decrypted(db, owner_id, _provider(engine_id))
    except Exception:  # noqa: BLE001  (a bad row must not break the registry)
        return None
    if found is None:
        return None
    try:
        value = json.loads(found[0])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def save(db: AsyncSession, owner_id: str, engine_id: str, config: dict) -> None:
    await user_credential_service.save_token(
        db,
        user_id=owner_id,
        provider=_provider(engine_id),
        file_path=_NOT_A_FILE,
        plaintext=json.dumps(config, ensure_ascii=False),
    )


async def remove(db: AsyncSession, owner_id: str, engine_id: str) -> None:
    await begin_write(db)
    await db.execute(
        delete(UserCredential).where(
            UserCredential.user_id == owner_id, UserCredential.provider == _provider(engine_id)
        )
    )
    await db.commit()
