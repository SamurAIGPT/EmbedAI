"""Automatic retry of failed routine runs (docs/ROUTINES.md, "Retries"):
off by default, bounded by the routine's retry_limit, started after its
retry_delay_minutes, never after a success, a Stop or a pause, and claimed by
exactly one scheduler when two race."""

import asyncio
from datetime import timedelta

import pytest

from app.db.models import Routine, RoutineRun, Turn
from app.services import routine_service as service
from tests.services.test_routine_service import (  # noqa: F401
    T0,
    Clock,
    agent,
    agent_args,
    clock,
    local_enabled,
    make_routine,
    notes,
    reload,
    routine_cleanup,
    runs_of,
    scheduler,
    settled,
)
from tests.services.thread_harness import (  # noqa: F401
    cleanup,
    cli,
    owner,
    rt,
    sessions,
    wait_until,
)

FAIL = {"type": "error_result"}
OK = {"type": "chat"}


async def manual(sessions, owner, agent, clock, **data):
    return await make_routine(sessions, owner, agent, clock, schedule_kind="manual", schedule_spec={}, **data)


async def start_now(sessions, owner, routine):
    async with sessions() as db:
        return await service.run_now(db, owner, routine.id, now=T0)


async def test_a_failed_run_is_retried_once_after_the_delay(scheduler, sessions, owner, agent, clock, cli):
    cli.script(FAIL, OK)
    routine = await manual(sessions, owner, agent, clock, retry_limit=1, retry_delay_minutes=2)
    first = await start_now(sessions, owner, routine)
    failed = await settled(scheduler, sessions, first.id)
    assert failed.state == "failed" and failed.attempt == 1

    first, retry = await runs_of(sessions, routine.id)
    assert (retry.trigger, retry.state, retry.attempt, retry.retry_of) == ("retry", "queued", 2, first.id)
    assert retry.scheduled_for == T0 + timedelta(minutes=2)
    assert retry.prompt.startswith("[Attempt 2: an earlier attempt of this run failed.")
    assert retry.prompt.endswith("Say hello")

    clock.advance(minutes=1)
    await scheduler.tick()
    assert (await reload(sessions, RoutineRun, retry.id)).state == "queued"  # not before its delay

    clock.advance(minutes=1, seconds=1)
    done = await settled(scheduler, sessions, retry.id)
    assert done.state == "completed" and done.thread_id != first.thread_id
    assert len(cli.log("start")) == 2
    assert [r.id for r in await runs_of(sessions, routine.id)] == [first.id, retry.id]
    assert await notes(sessions, owner) == ["routine_failed", "routine_completed"]


async def test_retries_stop_at_the_limit(scheduler, sessions, owner, agent, clock, cli):
    cli.script(FAIL)
    routine = await manual(sessions, owner, agent, clock, retry_limit=2, retry_delay_minutes=1)
    run = await start_now(sessions, owner, routine)
    for _ in range(3):
        await settled(scheduler, sessions, run.id)
        clock.advance(minutes=1, seconds=1)
        await scheduler.tick()
        run = (await runs_of(sessions, routine.id))[-1]
    runs = await runs_of(sessions, routine.id)
    assert [(r.attempt, r.state) for r in runs] == [(1, "failed"), (2, "failed"), (3, "failed")]
    assert runs[2].prompt.startswith("[Attempt 3:") and runs[2].prompt.count("[Attempt") == 1
    assert len(cli.log("start")) == 3


async def test_retry_is_off_by_default(scheduler, sessions, owner, agent, clock, cli):
    cli.script(FAIL)
    routine = await manual(sessions, owner, agent, clock)
    assert routine.retry_limit == 0 and routine.retry_delay_minutes == 5
    run = await start_now(sessions, owner, routine)
    await settled(scheduler, sessions, run.id)
    clock.advance(minutes=10)
    await scheduler.tick()
    assert [r.state for r in await runs_of(sessions, routine.id)] == ["failed"]


async def test_a_successful_run_is_not_retried(scheduler, sessions, owner, agent, clock, cli):
    routine = await manual(sessions, owner, agent, clock, retry_limit=3, retry_delay_minutes=1)
    run = await start_now(sessions, owner, routine)
    assert (await settled(scheduler, sessions, run.id)).state == "completed"
    clock.advance(minutes=5)
    await scheduler.tick()
    assert [r.id for r in await runs_of(sessions, routine.id)] == [run.id]
    assert len(cli.log("start")) == 1


async def test_a_run_stopped_by_its_run_limit_is_not_retried(scheduler, sessions, owner, agent, clock, cli, tmp_path):
    cli.script({"type": "chat", "wait_for": str(tmp_path / "never")})
    routine = await manual(sessions, owner, agent, clock, run_limit_minutes=1, retry_limit=3, retry_delay_minutes=1)
    run = await start_now(sessions, owner, routine)
    await scheduler.tick()
    clock.advance(seconds=61)
    await scheduler.tick()
    assert (await reload(sessions, RoutineRun, run.id)).state == "failed"
    clock.advance(minutes=5)
    await scheduler.tick()
    assert [r.id for r in await runs_of(sessions, routine.id)] == [run.id]


async def test_pausing_cancels_a_waiting_retry(scheduler, sessions, owner, agent, clock, cli):
    cli.script(FAIL, OK)
    routine = await manual(sessions, owner, agent, clock, retry_limit=1, retry_delay_minutes=1)
    run = await start_now(sessions, owner, routine)
    await settled(scheduler, sessions, run.id)
    async with sessions() as db:
        await service.set_paused(db, owner, routine.id, True, now=clock())
    _, retry = await runs_of(sessions, routine.id)
    assert retry.state == "skipped" and "paused" in retry.error
    clock.advance(minutes=2)
    await scheduler.tick()
    assert len(cli.log("start")) == 1


async def test_two_schedulers_racing_start_a_due_retry_once(rt, sessions, owner, agent, clock, cli):
    cli.script(FAIL, OK)
    first_scheduler = service.RoutineScheduler(runtime=rt, clock=clock)
    second_scheduler = service.RoutineScheduler(runtime=rt, clock=clock)
    routine = await manual(sessions, owner, agent, clock, retry_limit=1, retry_delay_minutes=1)
    run = await start_now(sessions, owner, routine)
    await settled(first_scheduler, sessions, run.id)
    clock.advance(minutes=1, seconds=1)

    # Both find the due retry and try to start it; the conditional claim
    # (queued -> running) lets exactly one through.
    await asyncio.gather(first_scheduler._start_queued(), second_scheduler._start_queued())
    _, retry = await runs_of(sessions, routine.id)
    assert retry.state == "running" and retry.turn_id is not None
    assert (await settled(first_scheduler, sessions, retry.id)).state == "completed"
    assert len(cli.log("start")) == 2  # the first attempt and one retry, not two


async def test_a_failed_run_gets_at_most_one_retry_row(rt, sessions, owner, agent, clock):
    routine = await manual(sessions, owner, agent, clock, retry_limit=3, retry_delay_minutes=1)
    async with sessions() as db:
        failed = RoutineRun(routine_id=routine.id, trigger="manual", state="failed", prompt="x", finished_at=T0)
        db.add(failed)
        await db.commit()
    texts = []
    for _ in range(2):  # two schedulers settling the same failure, one after the other
        sch = service.RoutineScheduler(runtime=rt, clock=clock)
        async with sessions() as db:
            row = await db.get(RoutineRun, failed.id)
            texts.append(await sch._queue_retry(db, await db.get(Routine, routine.id), row, clock()))
            await db.commit()
    assert "attempt 2 of 4" in texts[0] and texts[1] == ""
    runs = await runs_of(sessions, routine.id)
    assert [(r.trigger, r.retry_of) for r in runs] == [("manual", None), ("retry", failed.id)]


async def test_retry_settings_are_bounded_and_exported(sessions, owner, agent, clock):
    for data in ({"retry_limit": 4}, {"retry_limit": -1}, {"retry_delay_minutes": 0}):
        with pytest.raises(service.InvalidRoutine) as caught:
            await manual(sessions, owner, agent, clock, **data)
        assert caught.value.code == "invalid_limit"
    routine = await manual(sessions, owner, agent, clock, retry_limit=2, retry_delay_minutes=15)
    document = service.export_document(routine)
    assert (document["retry_limit"], document["retry_delay_minutes"]) == (2, 15)
    async with sessions() as db:
        imported = await service.import_document(db, owner, agent.id, document, now=clock())
    assert (imported.retry_limit, imported.retry_delay_minutes) == (2, 15)


async def test_a_retry_left_waiting_over_a_long_stop_is_missed_not_run(rt, sessions, owner, agent, clock, cli):
    cli.script(FAIL, OK)
    scheduler = service.RoutineScheduler(runtime=rt, clock=clock)
    routine = await manual(sessions, owner, agent, clock, retry_limit=1, retry_delay_minutes=1)
    run = await start_now(sessions, owner, routine)
    await settled(scheduler, sessions, run.id)
    clock.advance(days=3)  # the app was off; the retry is far past the catch-up window
    restarted = service.RoutineScheduler(runtime=rt, clock=clock)
    await restarted.recover()
    await restarted.tick()
    _, retry = await runs_of(sessions, routine.id)
    assert retry.state == "missed" and retry.thread_id is None
    assert len(cli.log("start")) == 1


async def test_a_due_occurrence_replaces_a_waiting_retry(rt, sessions, owner, agent, clock, cli):
    """Hourly routine, retry delay longer than the gap to the next occurrence
    (and the same after a restart): the fresh occurrence runs, the retry is
    recorded as replaced, nothing is skipped as "still in progress"."""
    cli.script(FAIL, OK)
    scheduler = service.RoutineScheduler(runtime=rt, clock=clock)
    routine = await make_routine(
        sessions, owner, agent, clock, schedule_kind="cron", schedule_spec={"expression": "0 * * * *"},
        retry_limit=1, retry_delay_minutes=120,
    )
    clock.advance(hours=1, seconds=5)
    await scheduler.tick()
    first = (await runs_of(sessions, routine.id))[0]
    await settled(scheduler, sessions, first.id)
    clock.advance(hours=1)
    await scheduler.tick()
    first, retry, fresh = await runs_of(sessions, routine.id)
    assert retry.trigger == "retry" and retry.state == "skipped" and retry.error == service.RETRY_REPLACED
    assert fresh.trigger == "schedule" and fresh.state in ("running", "completed")
    assert (await settled(scheduler, sessions, fresh.id)).state == "completed"


async def test_configuration_failures_are_not_retried(scheduler, sessions, owner, agent, clock, monkeypatch):
    async def unknown_engine(*_, **__):
        raise service.runtime_module.UnknownProvider("nope")

    monkeypatch.setattr(scheduler.runtime, "create_thread", unknown_engine)
    routine = await manual(sessions, owner, agent, clock, retry_limit=3, retry_delay_minutes=1)
    run = await start_now(sessions, owner, routine)
    await scheduler.tick()
    assert (await reload(sessions, RoutineRun, run.id)).state == "failed"
    assert [r.id for r in await runs_of(sessions, routine.id)] == [run.id]


async def test_two_schedulers_following_the_same_failed_turn_queue_one_retry(rt, sessions, owner, agent, clock, cli):
    cli.script(FAIL)
    first_scheduler = service.RoutineScheduler(runtime=rt, clock=clock)
    second_scheduler = service.RoutineScheduler(runtime=rt, clock=clock)
    routine = await manual(sessions, owner, agent, clock, retry_limit=1, retry_delay_minutes=1)
    run = await start_now(sessions, owner, routine)
    await first_scheduler._start_queued()
    turn_id = (await reload(sessions, RoutineRun, run.id)).turn_id

    async def turn_over():
        return (await reload(sessions, Turn, turn_id)).status != "running"

    await wait_until(turn_over)
    await asyncio.gather(first_scheduler._follow(run.id), second_scheduler._follow(run.id))
    runs = await runs_of(sessions, routine.id)
    assert [(r.state, r.retry_of) for r in runs] == [("failed", None), ("queued", run.id)]
    assert await notes(sessions, owner) == ["routine_failed"]
