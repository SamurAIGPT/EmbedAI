"""Database engines for the app, migrations and tests.

PostgreSQL engines are created exactly as before. SQLite needs a few settings
that are easy to get wrong, so they are applied here and nowhere else.
"""

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings

# How long a writer waits for another writer before "database is locked".
SQLITE_BUSY_TIMEOUT_MS = 5000

# Connection execution option that makes the next SQLite transaction start with
# BEGIN IMMEDIATE (see app/db/transactions.py).
SQLITE_BEGIN_OPTION = "sqlite_begin"


def is_sqlite(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def prepare_sqlite_file(url: str) -> None:
    """Create the database directory and file before first use. The file holds
    encrypted credentials, so where the OS supports it only the owner may read
    it (POSIX file modes; Windows uses the profile folder's access rules)."""
    database = make_url(url).database
    if not database or database == ":memory:":
        return
    path = Path(database)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))


def configure_sqlite(engine: Engine) -> None:
    """Settings every SQLite connection needs. `engine` is the synchronous
    engine (for an async engine, pass `async_engine.sync_engine`)."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        # Take over transaction control: the driver's implicit BEGIN does not
        # cooperate with SAVEPOINTs or DDL, so BEGIN is issued below instead.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        # WAL: readers and the single writer do not block each other.
        cursor.execute("PRAGMA journal_mode=WAL")
        # SQLite does not enforce foreign keys unless asked; PostgreSQL does.
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection):
        # A write transaction (app/db/transactions.begin_write) takes the write
        # lock up front, so it waits for other writers instead of failing later
        # when it tries to upgrade from a read.
        immediate = connection.get_execution_options().get(SQLITE_BEGIN_OPTION) == "IMMEDIATE"
        connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


def create_app_async_engine(url: str | None = None, **kwargs) -> AsyncEngine:
    url = url or settings.ASYNC_DATABASE_URL
    if not is_sqlite(url):
        return create_async_engine(url, **kwargs)
    prepare_sqlite_file(url)
    engine = create_async_engine(url, **kwargs)
    configure_sqlite(engine.sync_engine)
    return engine


def create_app_sync_engine(url: str | None = None, **kwargs) -> Engine:
    url = url or settings.SYNC_DATABASE_URL
    if not is_sqlite(url):
        return create_engine(url, **kwargs)
    prepare_sqlite_file(url)
    engine = create_engine(url, **kwargs)
    configure_sqlite(engine)
    return engine
