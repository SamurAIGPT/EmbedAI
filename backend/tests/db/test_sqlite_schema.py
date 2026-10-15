"""A new SQLite database: created by the real migration entry point, equal to
what the models describe, and usable by the app's own models. A database that
is not ours is refused and left exactly as it was."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.engine import create_app_async_engine, create_app_sync_engine
from app.db.models import PermissionAsk, Schedule, Tasks
from app.db.sqlite_baseline import BASELINE_REVISION, BASELINE_TABLES, _BASELINE_SQL
from tests.db.schema_description import describe
from tests.db.sqlite_helpers import run_alembic


def sqlite_engine(path):
    return create_app_sync_engine(f"sqlite:///{path.as_posix()}")


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    path = tmp_path_factory.mktemp("sqlite_schema") / "app.db"
    result = run_alembic(path, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    return path


def version_of(path) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        connection.close()


def test_new_database_has_the_models_schema_including_defaults(migrated, tmp_path):
    with_models = sqlite_engine(tmp_path / "from_models.db")
    upgraded = sqlite_engine(migrated)
    try:
        Base.metadata.create_all(with_models)
        assert describe(upgraded) == describe(with_models)
        assert set(describe(upgraded)) == set(Base.metadata.tables)
    finally:
        with_models.dispose()
        upgraded.dispose()


def test_the_baseline_lists_exactly_the_tables_it_creates():
    created = {
        line.split()[2]
        for line in _BASELINE_SQL.splitlines()
        if line.startswith("CREATE TABLE ")
    }
    assert created == set(BASELINE_TABLES)


def test_new_database_is_stamped_and_up_to_date(migrated):
    version = version_of(migrated)
    assert version in run_alembic(migrated, "heads").stdout
    assert BASELINE_REVISION == "d2fc641cb3d1"  # the revision the baseline represents


def test_running_the_migration_again_changes_nothing(migrated):
    engine = sqlite_engine(migrated)
    try:
        schema_before, version_before = describe(engine), version_of(migrated)
        again = run_alembic(migrated, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        assert describe(engine) == schema_before
        assert version_of(migrated) == version_before
    finally:
        engine.dispose()


def test_an_empty_file_is_initialised(tmp_path):
    path = tmp_path / "empty.db"
    path.write_bytes(b"")
    result = run_alembic(path, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert version_of(path) in run_alembic(path, "heads").stdout  # baseline, then every later revision


# --- refusing a database that is not ours ---------------------------------------


def make_unversioned(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE something (id INTEGER)")
    connection.execute("INSERT INTO something VALUES (1)")
    connection.commit()
    connection.close()


def make_empty_version_table(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    connection.commit()
    connection.close()


def make_partially_initialised(path):
    """A version stamp but only some of the baseline tables."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    connection.execute(f"INSERT INTO alembic_version VALUES ('{BASELINE_REVISION}')")
    connection.execute("CREATE TABLE task (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()


def make_not_a_database(path):
    path.write_bytes(b"this is definitely not a sqlite database " * 40)


@pytest.mark.parametrize("make, hint", [
    (make_unversioned, "no alembic_version"),
    (make_empty_version_table, "empty alembic_version"),
    (make_partially_initialised, "missing tables"),
    (make_not_a_database, "not a usable SQLite database"),
])
def test_a_database_that_is_not_ours_is_refused_and_left_untouched(tmp_path, make, hint):
    path = tmp_path / "foreign.db"
    make(path)
    before = path.read_bytes()
    files_before = sorted(p.name for p in tmp_path.iterdir())

    result = run_alembic(path, "upgrade", "head")

    assert result.returncode != 0
    assert hint in result.stderr
    assert path.read_bytes() == before  # not one byte changed
    assert sorted(p.name for p in tmp_path.iterdir()) == files_before  # no -wal/-shm/journal left behind


# --- the app's own models on the new database ----------------------------------


@pytest.fixture
async def sessions(migrated):
    engine = create_app_async_engine(f"sqlite+aiosqlite:///{migrated.as_posix()}")
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def make_task(**values) -> Tasks:
    base = dict(user_id="u", provider="claude", box_id="b", prompt_text="p", status="queued")
    return Tasks(**{**base, **values})


async def test_rows_get_ids_and_timezone_aware_timestamps(sessions):
    async with sessions() as db:
        first, second = make_task(), make_task()
        db.add_all([first, second])
        await db.commit()
        assert first.id is not None and second.id == first.id + 1
        await db.refresh(first)
        assert first.created_at.tzinfo is not None
        assert abs(datetime.now(UTC) - first.created_at) < timedelta(minutes=1)


async def test_schedules_are_found_by_an_aware_due_time(sessions):
    now = datetime.now(UTC)
    async with sessions() as db:
        db.add(Schedule(
            user_id="due-user", provider="claude", box_id="b", prompt_text="p",
            cron_expression="* * * * *", next_run_at=now - timedelta(minutes=1),
        ))
        db.add(Schedule(
            user_id="later-user", provider="claude", box_id="b", prompt_text="p",
            cron_expression="* * * * *", next_run_at=now + timedelta(hours=1),
        ))
        await db.commit()
        due = (await db.execute(
            select(Schedule.user_id).where(Schedule.user_id.in_(["due-user", "later-user"]), Schedule.next_run_at <= now)
        )).scalars().all()
    assert due == ["due-user"]


async def test_foreign_keys_are_enforced(sessions):
    async with sessions() as db:
        db.add(PermissionAsk(task_id=987654321, user_id="u", bucket="b"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_one_task_per_scheduled_occurrence(sessions):
    when = datetime(2030, 1, 1, tzinfo=UTC)
    async with sessions() as db:
        schedule = Schedule(
            user_id="u", provider="claude", box_id="b", prompt_text="p",
            cron_expression="* * * * *", next_run_at=when,
        )
        db.add(schedule)
        await db.flush()
        db.add(make_task(schedule_id=schedule.id, scheduled_for=when))
        await db.commit()
        db.add(make_task(schedule_id=schedule.id, scheduled_for=when))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
        # manual tasks (no schedule) are unaffected: NULLs are distinct
        db.add_all([make_task(), make_task()])
        await db.commit()
