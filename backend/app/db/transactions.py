"""Transaction helpers for code whose correctness depends on row locks or on
not holding a database snapshot open.

PostgreSQL lets a transaction read, then lock and write, and other writers wait
on the row (`SELECT ... FOR UPDATE`); every statement also sees the latest
committed data. SQLite has no row locks and one writer at a time, and a
transaction keeps the snapshot of its first read: it never sees later commits,
and a write after another connection committed fails with "database is locked".
These helpers are the SQLite counterparts. They do nothing on PostgreSQL.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import SQLITE_BEGIN_OPTION


def _is_sqlite(db: AsyncSession) -> bool:
    return db.get_bind().dialect.name == "sqlite"


def _has_pending_changes(db: AsyncSession) -> bool:
    return bool(db.new or db.dirty or db.deleted)


async def _in_write_transaction(db: AsyncSession) -> bool:
    connection = await db.connection()
    return connection.sync_connection.get_execution_options().get(SQLITE_BEGIN_OPTION) == "IMMEDIATE"


async def release_snapshot(db: AsyncSession) -> None:
    """End a read-only transaction before waiting on something slow (a network
    call, a subprocess, a sleep). Call it after the reads and before the wait:
    values already loaded stay usable, and the next query starts a fresh,
    current transaction. A session with pending changes is left alone (it must
    not be committed behind the caller's back)."""
    if not _is_sqlite(db) or not db.in_transaction() or _has_pending_changes(db):
        return
    await db.commit()


async def begin_write(db: AsyncSession) -> None:
    """Start the session's next transaction as a write transaction. Call it
    before the transaction's first query.

    On SQLite this takes the write lock immediately (BEGIN IMMEDIATE): other
    writers wait up to the busy timeout and then see this transaction's
    result, so a check-then-insert sequence cannot interleave. A read-only
    transaction the session already has open is ended first, because it holds
    an older snapshot. A session with pending changes cannot be committed
    here, so it keeps its current transaction. Calling it again inside a
    write transaction does nothing.
    """
    if not _is_sqlite(db):
        return
    if db.in_transaction():
        if await _in_write_transaction(db) or _has_pending_changes(db):
            return
        await db.commit()
    await db.connection(execution_options={SQLITE_BEGIN_OPTION: "IMMEDIATE"})
