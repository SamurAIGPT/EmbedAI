"""The in-app notification feed, plus an optional copy to one Telegram chat.

Rows are written inside the caller's transaction (so a notification exists
exactly when the change it reports was committed). Telegram is sent after the
commit by `deliver`, never while a write lock is held, and a failure to send
changes nothing stored.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.notification import Notification
from app.db.transactions import begin_write
from app.services import notification_service

logger = get_logger(__name__)

TITLE_CHARS = 200
BODY_CHARS = 2000
TELEGRAM_CHARS = 3500


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def add(
    db: AsyncSession,
    *,
    owner_id: str,
    kind: str,
    title: str,
    body: str | None = None,
    routine_id: int | None = None,
    run_id: int | None = None,
    thread_id: int | None = None,
) -> Notification:
    """Stage a notification in `db`'s transaction; the caller commits."""
    notification = Notification(
        owner_id=owner_id,
        kind=kind,
        title=_clip(title, TITLE_CHARS) or kind,
        body=_clip(body, BODY_CHARS),
        routine_id=routine_id,
        run_id=run_id,
        thread_id=thread_id,
    )
    db.add(notification)
    return notification


def telegram_text(notification: Notification) -> str:
    text = notification.title if not notification.body else f"{notification.title}\n\n{notification.body}"
    return _clip(text, TELEGRAM_CHARS) or notification.title


async def deliver(texts: list[str]) -> int:
    """Send the texts of committed notifications (`telegram_text`) to
    ROUTINE_TELEGRAM_CHAT_ID, when it and the bot token are set. Returns how
    many were sent."""
    chat = settings.ROUTINE_TELEGRAM_CHAT_ID.strip()
    if not chat or not settings.TELEGRAM_BOT_TOKEN or not texts:
        return 0
    try:
        chat_id = int(chat)
    except ValueError:
        logger.warning("ROUTINE_TELEGRAM_CHAT_ID is not a number; Telegram copies are off")
        return 0
    sent = 0
    for text in texts:
        if await notification_service.send_telegram_message(chat_id, text):
            sent += 1
    return sent


async def list_feed(
    db: AsyncSession, owner_id: str, *, limit: int = 50, unread_only: bool = False
) -> list[Notification]:
    query = select(Notification).where(Notification.owner_id == owner_id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    query = query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
    return list((await db.execute(query)).scalars().all())


async def unread_count(db: AsyncSession, owner_id: str) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.owner_id == owner_id, Notification.read_at.is_(None))
    )
    return int(result.scalar_one())


async def mark_read(db: AsyncSession, owner_id: str, notification_id: int) -> bool:
    """True when the notification exists for this owner (read now or before)."""
    await begin_write(db)
    notification = await db.get(Notification, notification_id)
    if notification is None or notification.owner_id != owner_id:
        await db.rollback()
        return False
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
    await db.commit()
    return True


async def mark_all_read(db: AsyncSession, owner_id: str) -> int:
    await begin_write(db)
    result = await db.execute(
        update(Notification)
        .where(Notification.owner_id == owner_id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    return result.rowcount or 0
