"""The SQLite engine settings and the two dialect-aware column types."""

import stat
import sys
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Identity, MetaData, Table, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from app.db.engine import (
    SQLITE_BUSY_TIMEOUT_MS,
    create_app_async_engine,
    create_app_sync_engine,
    is_sqlite,
    prepare_sqlite_file,
)
from app.db.types import BigIntPK, UTCDateTime


def sqlite_url(path, driver="sqlite"):
    return f"{driver}:///{path.as_posix()}"


# --- engine ------------------------------------------------------------------


def test_is_sqlite_recognises_both_drivers_and_not_postgres():
    assert is_sqlite("sqlite:///x.db") and is_sqlite("sqlite+aiosqlite:///x.db")
    assert not is_sqlite("postgresql+asyncpg://u:p@localhost/db")


def test_connection_settings_are_applied_to_every_connection(tmp_path):
    engine = create_app_sync_engine(sqlite_url(tmp_path / "a" / "b" / "x.db"))
    try:
        for _ in range(2):  # a second connection, not just the first
            with engine.connect() as connection:
                pragma = lambda name: connection.execute(text(f"PRAGMA {name}")).scalar()  # noqa: E731
                assert pragma("journal_mode") == "wal"
                assert pragma("foreign_keys") == 1
                assert pragma("busy_timeout") == SQLITE_BUSY_TIMEOUT_MS
                assert pragma("synchronous") == 1  # NORMAL
    finally:
        engine.dispose()


async def test_async_engine_gets_the_same_settings(tmp_path):
    engine = create_app_async_engine(sqlite_url(tmp_path / "x.db", "sqlite+aiosqlite"))
    try:
        async with engine.connect() as connection:
            assert (await connection.execute(text("PRAGMA foreign_keys"))).scalar() == 1
            assert (await connection.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
            assert (await connection.execute(text("PRAGMA busy_timeout"))).scalar() == SQLITE_BUSY_TIMEOUT_MS
    finally:
        await engine.dispose()


def test_transactions_are_explicit_so_savepoints_and_ddl_work(tmp_path):
    engine = create_app_sync_engine(sqlite_url(tmp_path / "x.db"))
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
            connection.commit()
            connection.execute(text("INSERT INTO t (v) VALUES ('kept')"))
            savepoint = connection.begin_nested()
            connection.execute(text("INSERT INTO t (v) VALUES ('undone')"))
            savepoint.rollback()
            connection.commit()
            assert connection.execute(text("SELECT v FROM t")).scalars().all() == ["kept"]
    finally:
        engine.dispose()


def test_prepare_creates_the_directory_and_file_once(tmp_path):
    target = tmp_path / "new" / "dir" / "open-dots.db"
    url = sqlite_url(target)
    prepare_sqlite_file(url)
    assert target.is_file()
    target.write_bytes(b"existing")
    prepare_sqlite_file(url)
    assert target.read_bytes() == b"existing"  # never recreated or truncated


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes; Windows uses profile folder ACLs")
def test_new_database_file_is_private_to_the_owner(tmp_path):
    target = tmp_path / "private" / "open-dots.db"
    prepare_sqlite_file(sqlite_url(target))
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) & 0o077 == 0


def test_in_memory_database_is_left_alone():
    prepare_sqlite_file("sqlite://")
    prepare_sqlite_file("sqlite:///:memory:")


# --- types -------------------------------------------------------------------


def test_postgres_ddl_for_the_types_is_what_the_models_always_used():
    table = Table(
        "t", MetaData(),
        Column("id", BigIntPK, Identity(always=True), primary_key=True),
        Column("at", UTCDateTime, nullable=False),
    )
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "id BIGINT GENERATED ALWAYS AS IDENTITY" in ddl
    assert "at TIMESTAMP WITH TIME ZONE NOT NULL" in ddl


def test_sqlite_ddl_for_the_types():
    table = Table(
        "t", MetaData(),
        Column("id", BigIntPK, primary_key=True),
        Column("at", UTCDateTime),
    )
    ddl = str(CreateTable(table).compile(dialect=sqlite.dialect()))
    assert "id INTEGER" in ddl and "at DATETIME" in ddl


def test_postgres_values_pass_through_untouched():
    kind = UTCDateTime()
    pg = postgresql.dialect()
    aware = datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert kind.process_bind_param(aware, pg) is aware
    assert kind.process_result_value(aware, pg) is aware
    assert kind.process_bind_param(None, pg) is None


@pytest.fixture
def datetime_table(tmp_path):
    metadata = MetaData()
    table = Table("events", metadata, Column("id", BigIntPK, primary_key=True), Column("at", UTCDateTime))
    engine = create_app_sync_engine(sqlite_url(tmp_path / "t.db"))
    metadata.create_all(engine)
    yield engine, table
    engine.dispose()


def test_sqlite_returns_timezone_aware_utc(datetime_table):
    engine, table = datetime_table
    ist = timezone(timedelta(hours=5, minutes=30))
    moment = datetime(2026, 3, 1, 18, 30, 15, 123456, tzinfo=ist)
    with engine.begin() as connection:
        connection.execute(table.insert().values(at=moment))
        stored = connection.execute(select(table.c.at)).scalar_one()
    assert stored.tzinfo is not None
    assert stored == moment  # the same instant,
    assert stored.utcoffset() == timedelta(0)  # reported in UTC
    assert stored.microsecond == 123456


def test_a_naive_datetime_is_taken_as_utc(datetime_table):
    engine, table = datetime_table
    with engine.begin() as connection:
        connection.execute(table.insert().values(at=datetime(2026, 1, 1, 9, 0)))
        assert connection.execute(select(table.c.at)).scalar_one() == datetime(2026, 1, 1, 9, tzinfo=UTC)


def test_null_round_trips(datetime_table):
    engine, table = datetime_table
    with engine.begin() as connection:
        connection.execute(table.insert().values(at=None))
        assert connection.execute(select(table.c.at)).scalar_one() is None


def test_comparisons_use_the_instant_not_the_offset(datetime_table):
    """next_run_at <= now(UTC) is how schedules find due work."""
    engine, table = datetime_table
    base = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(table.insert(), [
            {"at": base - timedelta(minutes=1)},
            {"at": base},
            {"at": base + timedelta(minutes=1)},
        ])
        # The same instant written with a different offset still matches.
        ahead = base.astimezone(timezone(timedelta(hours=9)))
        due = connection.execute(select(table.c.at).where(table.c.at <= ahead).order_by(table.c.at)).scalars().all()
    assert len(due) == 2 and due[-1] == base


def test_ids_auto_increment(datetime_table):
    engine, table = datetime_table
    with engine.begin() as connection:
        first = connection.execute(table.insert().values(at=None)).inserted_primary_key[0]
        second = connection.execute(table.insert().values(at=None)).inserted_primary_key[0]
    assert (first, second) == (1, 2)
