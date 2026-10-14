"""The transaction helpers, and the concurrency behaviour that depends on them.

Most tests run on whichever database the suite uses; the ones that show the
SQLite problem itself (a stale snapshot, a lost race) run on SQLite only. Rows
are created with real committed transactions from separate connections, so each
test removes what it created."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.db.engine import create_app_async_engine
from app.db.models import PermissionAsk, Schedule, Tasks
from app.db.transactions import begin_write, release_snapshot

on_sqlite = settings.DATABASE_KIND == "sqlite"
sqlite_only = pytest.mark.skipif(not on_sqlite, reason="shows a SQLite-specific behaviour")


@pytest.fixture
async def sessions():
    engine = create_app_async_engine()
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def owner():
    """A unique user id; everything created for it is deleted afterwards."""
    return "od_txn_" + uuid4().hex


@pytest.fixture(autouse=True)
async def cleanup(sessions, owner):
    yield
    async with sessions() as db:
        await db.execute(delete(PermissionAsk).where(PermissionAsk.user_id == owner))
        await db.execute(delete(Tasks).where(Tasks.user_id == owner))
        await db.execute(delete(Schedule).where(Schedule.user_id == owner))
        await db.commit()


def new_task(owner, **values) -> Tasks:
    base = dict(user_id=owner, provider="claude", box_id="b", prompt_text="p", status="queued")
    return Tasks(**{**base, **values})


async def count_tasks(db, owner, **where) -> int:
    query = select(func.count()).select_from(Tasks).where(Tasks.user_id == owner)
    for column, value in where.items():
        query = query.where(getattr(Tasks, column) == value)
    return (await db.execute(query)).scalar_one()


# --- the helpers are no-ops on PostgreSQL ---------------------------------------


class FakePostgresSession:
    """Anything beyond get_bind() would fail: the helpers must not touch it."""

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))


async def test_helpers_do_nothing_on_postgresql():
    db = FakePostgresSession()
    await begin_write(db)
    await release_snapshot(db)


# --- release_snapshot ----------------------------------------------------------


async def test_after_release_a_session_sees_what_others_committed(sessions, owner):
    async with sessions() as reader, sessions() as writer:
        assert await count_tasks(reader, owner) == 0  # the reader's snapshot opens here
        writer.add(new_task(owner))
        await writer.commit()
        await release_snapshot(reader)
        assert await count_tasks(reader, owner) == 1


@sqlite_only
async def test_without_release_a_sqlite_session_keeps_seeing_the_old_data(sessions, owner):
    """Why release_snapshot exists: SQLite readers do not see later commits."""
    async with sessions() as reader, sessions() as writer:
        assert await count_tasks(reader, owner) == 0
        writer.add(new_task(owner))
        await writer.commit()
        assert await count_tasks(reader, owner) == 0  # stale


@sqlite_only
async def test_a_write_after_a_stale_read_fails_without_release_and_works_with_it(sessions, owner):
    async with sessions() as seed:
        seed.add(new_task(owner, status="queued"))
        await seed.commit()
    async with sessions() as db, sessions() as other:
        await count_tasks(db, owner)  # read: opens the snapshot
        other.add(new_task(owner))
        await other.commit()  # someone else commits
        db.add(new_task(owner))
        with pytest.raises(Exception, match="database is locked"):
            await db.commit()  # the upgrade from a stale read is refused
        await db.rollback()

        await count_tasks(db, owner)
        other.add(new_task(owner))
        await other.commit()
        await release_snapshot(db)  # what the app code now does before slow work
        db.add(new_task(owner))
        await db.commit()


async def test_release_keeps_loaded_values_usable(sessions, owner):
    async with sessions() as seed:
        seed.add(new_task(owner, prompt_text="keep me"))
        await seed.commit()
    async with sessions() as db:
        task = (await db.execute(select(Tasks).where(Tasks.user_id == owner))).scalar_one()
        await release_snapshot(db)
        assert task.prompt_text == "keep me"  # no lazy load, no expiry


@sqlite_only
async def test_release_leaves_pending_changes_alone(sessions, owner):
    async with sessions() as db:
        await count_tasks(db, owner)
        db.add(new_task(owner))
        await release_snapshot(db)  # must not commit what the caller has not finished
        async with sessions() as other:
            assert await count_tasks(other, owner) == 0
        await db.commit()
        async with sessions() as other:
            assert await count_tasks(other, owner) == 1


# --- begin_write ---------------------------------------------------------------


@sqlite_only
async def test_begin_write_makes_check_then_insert_deterministic(sessions, owner):
    """Eight requests all do: check for an active task, then insert. Exactly one wins."""

    async def admit(index):
        async with sessions() as db:
            await begin_write(db)
            active = await count_tasks(db, owner, session_id="conversation")
            await asyncio.sleep(0.05)  # widen the window between check and insert
            if active:
                return "refused"
            db.add(new_task(owner, session_id="conversation"))
            await db.commit()
            return "admitted"

    results = await asyncio.gather(*(admit(i) for i in range(8)))
    assert results.count("admitted") == 1 and results.count("refused") == 7
    async with sessions() as db:
        assert await count_tasks(db, owner, session_id="conversation") == 1


@sqlite_only
async def test_begin_write_ends_an_earlier_read_transaction(sessions, owner):
    async with sessions() as db, sessions() as other:
        await count_tasks(db, owner)  # a read first, like a router that looked something up
        other.add(new_task(owner))
        await other.commit()
        await begin_write(db)
        assert await count_tasks(db, owner) == 1  # sees the commit, no stale snapshot
        db.add(new_task(owner))
        await db.commit()


@sqlite_only
async def test_begin_write_keeps_a_session_that_has_pending_changes(sessions, owner):
    async with sessions() as db:
        await count_tasks(db, owner)
        db.add(new_task(owner))
        await begin_write(db)  # cannot commit for the caller; does not raise or lose the change
        await db.commit()
        assert await count_tasks(db, owner) == 1


@sqlite_only
async def test_begin_write_twice_is_harmless(sessions, owner):
    async with sessions() as db:
        await begin_write(db)
        db.add(new_task(owner))
        await db.flush()
        await begin_write(db)  # already in the write transaction; nothing is committed
        async with sessions() as other:
            # not visible to others until commit
            assert await count_tasks(other, owner) == 0
        await db.commit()
        assert await count_tasks(db, owner) == 1


@sqlite_only
async def test_a_second_writer_waits_for_the_first_instead_of_failing(sessions, owner):
    order = []

    async def first():
        async with sessions() as db:
            await begin_write(db)
            order.append("first has the lock")
            await asyncio.sleep(0.3)
            db.add(new_task(owner, prompt_text="first"))
            await db.commit()
            order.append("first committed")

    async def second():
        await asyncio.sleep(0.05)
        async with sessions() as db:
            await begin_write(db)  # waits for the first, within the busy timeout
            order.append("second has the lock")
            db.add(new_task(owner, prompt_text="second"))
            await db.commit()

    await asyncio.gather(first(), second())
    assert order == ["first has the lock", "first committed", "second has the lock"]


# --- services built on them ----------------------------------------------------


async def test_a_poll_sees_an_answer_committed_by_another_connection(sessions, owner):
    from app.services import permission_ask_service

    async with sessions() as seed:
        task = new_task(owner, status="running")
        seed.add(task)
        await seed.commit()
        ask = await permission_ask_service.create_ask(
            seed, owner, bucket="system", task_id=task.id, connector=None, tool=None
        )
        ask_id = ask.id

    async def answer_later():
        await asyncio.sleep(0.3)
        async with sessions() as db:
            await permission_ask_service.answer_ask(db, ask_id, "allow")

    async with sessions() as poller:
        answering = asyncio.create_task(answer_later())
        decision = await permission_ask_service.poll_for_answer(
            poller, ask_id, timeout_seconds=5, interval_seconds=0.1
        )
        await answering
    assert decision == "allow"
    async with sessions() as db:
        assert (await db.get(Tasks, task.id)).status == "running"  # resumed from waiting_approval


async def test_two_answers_to_one_ask_resolve_exactly_once(sessions, owner):
    from app.services import permission_ask_service

    async with sessions() as seed:
        task = new_task(owner, status="running")
        seed.add(task)
        await seed.commit()
        ask = await permission_ask_service.create_ask(
            seed, owner, bucket="system", task_id=task.id, connector=None, tool=None
        )
        ask_id = ask.id

    async def answer(decision):
        async with sessions() as db:
            return await permission_ask_service.answer_ask(db, ask_id, decision, always=True)

    allow, deny = await asyncio.gather(answer("allow"), answer("deny"))
    assert allow.decision == deny.decision  # both callers get the one recorded answer
    async with sessions() as db:
        from app.db.models import PermissionRule

        rules = (await db.execute(
            select(PermissionRule).where(PermissionRule.user_id == owner)
        )).scalars().all()
        assert len(rules) == 1 and rules[0].decision == allow.decision
        await db.execute(delete(PermissionRule).where(PermissionRule.user_id == owner))
        await db.commit()


async def test_an_expiring_ask_never_overwrites_an_answer(sessions, owner):
    from app.services import permission_ask_service

    async with sessions() as seed:
        task = new_task(owner, status="running")
        seed.add(task)
        await seed.commit()
        ask = await permission_ask_service.create_ask(
            seed, owner, bucket="system", task_id=task.id, connector=None, tool=None
        )
        ask_id = ask.id

    async def answer_at_the_deadline():
        await asyncio.sleep(0.18)
        async with sessions() as db:
            await permission_ask_service.answer_ask(db, ask_id, "allow")

    async with sessions() as poller:
        answering = asyncio.create_task(answer_at_the_deadline())
        decision = await permission_ask_service.poll_for_answer(
            poller, ask_id, timeout_seconds=0.2, interval_seconds=0.1
        )
        await answering
    async with sessions() as db:
        stored = await db.get(PermissionAsk, ask_id)
    # Whichever came first wins, and the result the hook got matches what is stored.
    assert (stored.status, decision) in {("answered", "allow"), ("expired", "deny")}


async def test_sync_reads_current_data_even_on_a_long_lived_session(sessions, owner):
    from app.services import task_execution_service

    async with sessions() as seed:
        task = new_task(owner, status="waiting_approval", prompt_id="1")
        seed.add(task)
        await seed.commit()
        task_id = task.id

    async with sessions() as poller, sessions() as other:
        loaded = await poller.get(Tasks, task_id)  # the caller's earlier read
        await other.execute(
            Tasks.__table__.update().where(Tasks.id == task_id).values(status="running")
        )
        await other.commit()
        synced = await task_execution_service.sync_task_status(poller, loaded)
        assert synced.status == "running"
