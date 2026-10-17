"""The routine scheduler's single-instance guard (app/core/scheduler_lock.py).

On SQLite it always holds (the file lease already means one backend). On
PostgreSQL two holders on one database never hold at once; a release, a closed
connection and a killed session each let the other one take over. The real
two-process check is tests/e2e/test_scheduler_lock.py.
"""

import asyncio

import pytest
from sqlalchemy import text

from app.core.scheduler_lock import SchedulerLock
from app.db.engine import create_app_async_engine
from tests.conftest import using_sqlite

# A key of its own, so a backend running against the same database during the
# test (or another test) is never disturbed.
TEST_KEY = 0x7465_7374_6C6F_636B

postgres_only = pytest.mark.skipif(using_sqlite(), reason="advisory locks are PostgreSQL's")


@pytest.mark.asyncio
async def test_sqlite_always_runs_the_scheduler():
    lock = SchedulerLock("sqlite")
    assert await lock.ensure() is True
    assert lock.held
    await lock.release()
    assert not lock.held


@postgres_only
@pytest.mark.asyncio
async def test_only_one_holder_and_a_release_hands_over():
    first_engine, second_engine = create_app_async_engine(), create_app_async_engine()
    first = SchedulerLock("postgres", first_engine, key=TEST_KEY)
    second = SchedulerLock("postgres", second_engine, key=TEST_KEY)
    try:
        assert await first.ensure() is True
        assert await second.ensure() is False
        # Asking again changes nothing for either.
        assert await first.ensure() is True
        assert await second.ensure() is False
        await first.release()
        assert not first.held
        assert await second.ensure() is True
        assert await first.ensure() is False
    finally:
        await first.release()
        await second.release()
        await first_engine.dispose()
        await second_engine.dispose()


@postgres_only
@pytest.mark.asyncio
async def test_a_killed_session_frees_the_lock_and_the_holder_notices():
    first_engine, second_engine, admin = (create_app_async_engine() for _ in range(3))
    first = SchedulerLock("postgres", first_engine, key=TEST_KEY)
    second = SchedulerLock("postgres", second_engine, key=TEST_KEY)
    try:
        assert await first.ensure() is True
        assert await second.ensure() is False
        holder_pid = await first._conn.scalar(text("SELECT pg_backend_pid()"))
        await first._conn.commit()
        # What the server does when the holding process dies: the session ends.
        async with admin.connect() as conn:
            assert await conn.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": holder_pid})
            await conn.commit()
        # The server ends the session a moment after it is asked to.
        for _ in range(100):
            if await second.ensure():
                break
            await asyncio.sleep(0.05)
        assert second.held
        # The old holder finds its connection gone, stops holding, and cannot
        # take the lock back while the new holder has it.
        assert await first.ensure() is False
        assert not first.held
    finally:
        await first.release()
        await second.release()
        for engine in (first_engine, second_engine, admin):
            await engine.dispose()


@pytest.mark.asyncio
async def test_an_unreachable_database_is_not_held():
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none")
    lock = SchedulerLock("postgres", engine, key=TEST_KEY)
    try:
        assert await lock.ensure() is False
        assert not lock.held
    finally:
        await lock.release()
        await engine.dispose()
