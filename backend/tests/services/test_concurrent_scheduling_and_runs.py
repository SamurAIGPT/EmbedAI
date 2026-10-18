"""Behaviour that used to rely on PostgreSQL row locks or on a transaction
staying open, checked with several real connections at once. Runs on whichever
database the suite uses (the same outcomes must hold on both)."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.engine import create_app_async_engine
from app.db.models import Schedule, Tasks
from app.services import local_claude_runner, schedule_service, task_execution_service


@pytest.fixture
async def sessions(monkeypatch):
    engine = create_app_async_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # The services open their own sessions from this factory.
    monkeypatch.setattr(schedule_service, "AsyncSessionLocal", factory)
    monkeypatch.setattr(task_execution_service, "AsyncSessionLocal", factory)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def owner():
    return "od_conc_" + uuid4().hex


@pytest.fixture(autouse=True)
async def cleanup(sessions, owner):
    yield
    async with sessions() as db:
        await db.execute(delete(Tasks).where(Tasks.user_id == owner))
        await db.execute(delete(Schedule).where(Schedule.user_id == owner))
        await db.commit()


async def add_due_schedules(sessions, owner, count) -> list[int]:
    due = datetime.now(UTC) - timedelta(seconds=30)
    async with sessions() as db:
        schedules = [
            Schedule(
                user_id=owner, provider="claude", box_id="bx_concurrent", prompt_text=f"run {i}",
                cron_expression="0 3 * * *", timezone="UTC", next_run_at=due,
            )
            for i in range(count)
        ]
        db.add_all(schedules)
        await db.commit()
        return [s.id for s in schedules]


@pytest.mark.parametrize("schedules, pollers", [(1, 5), (4, 4)])
async def test_concurrent_polls_start_each_due_schedule_exactly_once(
    sessions, owner, monkeypatch, schedules, pollers
):
    started = []

    async def record_start(db, task_id):
        started.append(task_id)

    monkeypatch.setattr(task_execution_service, "start_task", record_start)
    ids = await add_due_schedules(sessions, owner, schedules)

    await asyncio.gather(*(schedule_service.poll_due_schedules() for _ in range(pollers)))

    async with sessions() as db:
        per_schedule = dict((await db.execute(
            select(Tasks.schedule_id, func.count()).where(Tasks.user_id == owner).group_by(Tasks.schedule_id)
        )).all())
        due_again = (await db.execute(
            select(func.count()).select_from(Schedule).where(
                Schedule.user_id == owner, Schedule.next_run_at <= datetime.now(UTC)
            )
        )).scalar_one()
    assert per_schedule == {schedule_id: 1 for schedule_id in ids}  # one task per occurrence
    assert sorted(started) == sorted(
        (await _task_ids(sessions, owner))
    )  # and each was started once
    assert due_again == 0  # the next run was moved forward


async def _task_ids(sessions, owner):
    async with sessions() as db:
        return list((await db.execute(select(Tasks.id).where(Tasks.user_id == owner))).scalars())


async def test_a_local_run_survives_other_writes_while_it_runs(sessions, owner, monkeypatch):
    """The run holds no transaction while the CLI works, so another connection
    committing meanwhile cannot make the result unwritable."""
    async with sessions() as db:
        task = Tasks(
            user_id=owner, provider="claude-local", box_id="local", prompt_text="hi",
            status="running", session_id=str(uuid4()),
        )
        db.add(task)
        await db.commit()
        task_id = task.id

    async def run_while_others_write(prompt, session_id, on_stdout_line=None):
        async with sessions() as other:  # a different request commits during the run
            other.add(Tasks(user_id=owner, provider="claude", box_id="b", prompt_text="x", status="queued"))
            await other.commit()
        on_stdout_line(b'{"type": "result", "is_error": false, "result": "done"}')
        await asyncio.sleep(0)
        return local_claude_runner.RunOutput(stdout="", stderr="", exit_code=0)

    monkeypatch.setattr(local_claude_runner, "run_prompt", run_while_others_write)
    await task_execution_service._run_local_task(task_id)

    async with sessions() as db:
        finished = await db.get(Tasks, task_id)
    assert finished.status == "succeeded" and finished.result == "done"
