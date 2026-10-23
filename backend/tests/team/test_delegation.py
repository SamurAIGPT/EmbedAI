"""Non-blocking delegation: hand off, end the turn, resume once with results."""
import asyncio
import json
import pytest
from sqlalchemy import select, func, update
from app.core.config import settings
from app.db.models import Agent, AgentJob, AgentJobInbox, Event, Turn
from app.services import agent_store, thread_store
from app.services.provider_adapter import TurnOutcome
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from app.team import service
from tests.services.thread_harness import sessions, owner, cleanup, wait_until  # noqa: F401
from tests.team.test_collaboration import Engine


class FakeEngine(Engine):
    nested = None  # set by a test: records what a blocking job's own delegate call returns

    async def run_turn(self, ctx, sink, gate):
        if 'NESTED' in ctx.prompt and self.nested is not None:
            await self.nested(ctx)
        if ctx.thread_id not in self.handlers:
            if 'FAIL' in ctx.prompt:
                self.contexts.append(ctx)
                return TurnOutcome('failed', error='boom', error_code='provider_error')
            if 'ASK' in ctx.prompt:
                self.contexts.append(ctx)
                await sink.emit('assistant.message', {'text': 'partial'})
                return TurnOutcome('succeeded', result='partial work\nNEEDS INPUT: which environment?')
        return await super().run_turn(ctx, sink, gate)


@pytest.fixture
async def team(sessions, owner, monkeypatch, tmp_path):
    from app.services import local_claude_runner
    monkeypatch.setattr(local_claude_runner, 'workspace_dir', lambda: tmp_path)
    monkeypatch.setattr(settings, 'TEAM_ENABLED', True)
    engine = FakeEngine()
    rt = ThreadRuntime(hub=ThreadHub(), adapters={'claude': engine}, owner_id=owner, _session_factory=sessions)
    async with sessions() as db:
        lead = await agent_store.create_agent(db, owner_id=owner, name='Lead')
        one = await agent_store.create_agent(db, owner_id=owner, name='Reviewer', approval_mode='ask')
        two = await agent_store.create_agent(db, owner_id=owner, name='Drafter', approval_mode='ask')
        await service.set_lead(db, owner, lead.id)
        await service.change_member(db, owner, one.id)
        await service.change_member(db, owner, two.id)
    yield rt, engine, lead, one, two
    # Let background work finish before the database is cleaned.
    async with sessions() as db:
        live = list((await db.execute(select(AgentJob.id).where(AgentJob.owner_id == owner, AgentJob.status.in_(('queued', 'running', 'needs_input', 'waiting', 'resume'))))).scalars())
    for identifier in live:
        await rt.team_jobs.cancel(identifier)
    for _ in range(200):
        if not rt._runs:
            break
        await asyncio.sleep(.05)
    await rt.team_jobs.close()
    await rt.shutdown()
    await asyncio.gather(*rt.rooms.tasks.values(), return_exceptions=True)


def lead_script(rt, engine, thread, first):
    """The lead's first turn runs `first(run)`; later turns just answer."""
    calls = []
    async def handler(ctx, sink, gate):
        calls.append(ctx)
        if len(calls) == 1:
            await first(rt._runs[ctx.turn_id])
            return TurnOutcome('succeeded', result='Handed out')
        await sink.emit('assistant.message', {'text': 'Final answer'})
        return TurnOutcome('succeeded', result='Final answer')
    engine.handlers[thread.id] = handler
    return calls


async def turns_of(sessions, thread_id):
    async with sessions() as db:
        return list((await db.execute(select(Turn).where(Turn.thread_id == thread_id).order_by(Turn.id))).scalars())


async def root_of(sessions, thread_id):
    async with sessions() as db:
        return await db.scalar(select(AgentJob).where(AgentJob.requester_thread_id == thread_id, AgentJob.kind == 'root'))


async def test_delegate_returns_at_once_and_lead_is_resumed_exactly_once(team, sessions):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    receipts = {}
    async def first(run):
        receipts.update(await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'Review the patch', 'done_when': 'A verdict'}))
    calls = lead_script(rt, engine, thread, first)
    await rt.send_turn(thread.id, 'please review')
    async def done():
        root = await root_of(sessions, thread.id)
        return root and root.status == 'completed'
    await wait_until(done)
    assert receipts['receipts'][0]['status'] == 'assigned' and 'not completed' in receipts['note']
    turns = await turns_of(sessions, thread.id)
    assert len(turns) == 2 and len(calls) == 2
    assert 'Harness message' in turns[1].prompt_text and 'Specialist result' in turns[1].prompt_text
    assert 'Reviewer' in calls[0].prompt and 'delegate' in calls[0].prompt  # roster and lead paragraph
    async with sessions() as db:
        root = await root_of(sessions, thread.id)
        child = await db.scalar(select(AgentJob).where(AgentJob.parent_id == root.id))
        assert (child.status, child.outcome, child.reply, child.depth) == ('completed', 'done', 'resume', 1)
        items = list((await db.execute(select(AgentJobInbox).where(AgentJobInbox.holder_id == root.id))).scalars())
        assert len(items) == 1 and items[0].delivered_at is not None
        assert root.result == 'Final answer' and root.executions == 1


async def test_two_teammates_settle_into_one_resume(team, sessions):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    async def first(run):
        out = await rt.team_jobs.delegate(run, {'agent_ids': [one.id, two.id], 'brief': 'Split the work'})
        assert [r['status'] for r in out['receipts']] == ['assigned', 'assigned']
    lead_script(rt, engine, thread, first)
    await rt.send_turn(thread.id, 'go')
    async def done():
        root = await root_of(sessions, thread.id)
        return root and root.status == 'completed'
    await wait_until(done)
    turns = await turns_of(sessions, thread.id)
    assert len(turns) == 2
    body = json.loads(turns[1].prompt_text.split('\n', 1)[1])
    assert len(body['reports']) == 2 and {r['teammate'] for r in body['reports']} == {'Reviewer', 'Drafter'}


async def test_failed_job_is_reported_and_asking_again_runs_again(team, sessions):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    ids = []
    async def first(run):
        out = await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'FAIL this one'})
        ids.append(out['receipts'][0]['job_id'])
    lead_script(rt, engine, thread, first)
    await rt.send_turn(thread.id, 'go')
    async def done():
        root = await root_of(sessions, thread.id)
        return root and root.status == 'completed'
    await wait_until(done)
    turns = await turns_of(sessions, thread.id)
    assert '"status": "failed"' in turns[1].prompt_text and 'provider_error' in turns[1].prompt_text
    # The failed job is not returned as if it were an answer.
    thread2 = await rt.create_thread(agent_id=lead.id)
    again = []
    async def second(run):
        again.append(await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'FAIL this one'}))
        again.append(await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'FAIL this one'}))
    lead_script(rt, engine, thread2, second)
    await rt.send_turn(thread2.id, 'go again')
    await wait_until(lambda: len(again) == 2)
    assert again[0]['receipts'][0]['job_id'] == again[1]['receipts'][0]['job_id'] != ids[0]


async def test_question_parks_then_the_persons_answer_runs_the_job_again(team, sessions):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    async def first(run):
        await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'ASK about deploy'})
    calls = lead_script(rt, engine, thread, first)
    await rt.send_turn(thread.id, 'deploy it')
    async def parked():
        async with sessions() as db:
            return await db.scalar(select(AgentJob).where(AgentJob.status == 'needs_input', AgentJob.question.is_not(None), AgentJob.requester_thread_id == thread.id))
    job = await wait_until(parked)
    assert job.question == 'which environment?'
    await wait_until(lambda: len(calls) == 2)  # the lead is resumed with the question, once
    turns = await turns_of(sessions, thread.id)
    assert 'which environment?' in turns[1].prompt_text and 'questions' in turns[1].prompt_text
    root = await root_of(sessions, thread.id)
    async def waiting():
        async with sessions() as db:
            return (await db.get(AgentJob, root.id)).status == 'waiting'
    await wait_until(waiting)
    # The person answers in the teammate's own conversation.
    await rt.send_turn(job.assignee_thread_id, 'staging')
    async def finished():
        async with sessions() as db:
            return (await db.get(AgentJob, root.id)).status == 'completed'
    await wait_until(finished)
    assert len(await turns_of(sessions, thread.id)) == 3
    async with sessions() as db:
        done = await db.get(AgentJob, job.id)
        assert done.status == 'completed' and done.answer == 'staging'


async def test_stop_while_waiting_cancels_and_never_resumes(team, sessions):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    async def first(run):
        await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'BLOCK forever'})
    lead_script(rt, engine, thread, first)
    await rt.send_turn(thread.id, 'go')
    async def waiting():
        root = await root_of(sessions, thread.id)
        return root and root.status == 'waiting'
    await wait_until(waiting)
    await rt.stop_turn(thread.id)
    async with sessions() as db:
        root = await root_of(sessions, thread.id)
        child = await db.scalar(select(AgentJob).where(AgentJob.parent_id == root.id))
        assert root.status == 'cancelled' and child.status == 'cancelled'
    await wait_until(lambda: not rt._runs)
    await rt.team_jobs.tick()
    assert len(await turns_of(sessions, thread.id)) == 1


async def test_budget_and_parallel_limits(team, sessions, monkeypatch):
    rt, engine, lead, one, two = team
    monkeypatch.setattr(settings, 'TEAM_MAX_PARALLEL', 1)
    thread = await rt.create_thread(agent_id=lead.id)
    async def first(run):
        await rt.team_jobs.delegate(run, {'agent_ids': [one.id, two.id], 'brief': 'Two at once'})
    lead_script(rt, engine, thread, first)
    await rt.send_turn(thread.id, 'go')
    async def done():
        root = await root_of(sessions, thread.id)
        return root and root.status == 'completed'
    await wait_until(done)
    assert len(await turns_of(sessions, thread.id)) == 2  # both results, one resume
    monkeypatch.setattr(settings, 'TEAM_MAX_JOBS_PER_ROOT', 1)
    thread2 = await rt.create_thread(agent_id=lead.id)
    out = {}
    async def second(run):
        out.update(await rt.team_jobs.delegate(run, {'agent_ids': [one.id, two.id], 'brief': 'Over budget'}))
    lead_script(rt, engine, thread2, second)
    await rt.send_turn(thread2.id, 'again')
    await wait_until(lambda: out)
    assert [r.get('code') for r in out['receipts']] == [None, 'root_budget']


async def test_resume_that_cannot_start_is_retried_not_lost(team, sessions, monkeypatch):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    async def first(run):
        await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'Do it'})
    lead_script(rt, engine, thread, first)
    real = rt.send_turn
    state = {'failed': 0}
    async def flaky(thread_id, text, **kw):
        if thread_id == thread.id and text.startswith('Harness message') and state['failed'] < 2:
            state['failed'] += 1
            raise thread_store.ThreadBusy(thread_id)
        return await real(thread_id, text, **kw)
    monkeypatch.setattr(rt, 'send_turn', flaky)
    await rt.send_turn(thread.id, 'go')
    async def done():
        await rt.team_jobs.tick()  # no scheduler runs here: a refused resume is retried by the next pass
        root = await root_of(sessions, thread.id)
        return root and root.status == 'completed'
    await wait_until(done)
    assert state['failed'] == 2
    turns = await turns_of(sessions, thread.id)
    assert len(turns) == 2
    async with sessions() as db:
        root = await root_of(sessions, thread.id)
        assert (await db.scalar(select(func.count()).select_from(AgentJobInbox).where(AgentJobInbox.holder_id == root.id, AgentJobInbox.delivered_at.is_(None)))) == 0


async def test_interrupted_child_is_failed_and_the_lead_is_told(team, sessions):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    async def first(run):
        await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'BLOCK a while'})
    lead_script(rt, engine, thread, first)
    await rt.send_turn(thread.id, 'go')
    async def running():
        async with sessions() as db:
            return await db.scalar(select(AgentJob).where(AgentJob.requester_thread_id == thread.id, AgentJob.kind == 'job', AgentJob.current_turn_id.is_not(None)))
    child = await wait_until(running)
    # The process "died": the turn is interrupted after work began, then it is swept.
    async with sessions() as db:
        await thread_store._add_event(db, child.assignee_thread_id, 'assistant.message', {'text': 'partway'}, child.current_turn_id)
        await thread_store._interrupt(db, child.current_turn_id, reason='lease_expired')
    active = rt._runs.get(child.current_turn_id)
    if active:
        active.ctx.abort()
    async def settled():
        async with sessions() as db:
            return (await db.get(AgentJob, child.id)).status == 'failed'
    for _ in range(40):
        await rt.team_jobs.recover()
        if await settled():
            break
        await asyncio.sleep(.1)
    async with sessions() as db:
        job = await db.get(AgentJob, child.id)
        assert (job.status, job.outcome) == ('failed', 'interrupted')
    async def done():
        root = await root_of(sessions, thread.id)
        return root and root.status == 'completed'
    await wait_until(done)
    assert 'interrupted' in (await turns_of(sessions, thread.id))[1].prompt_text


async def test_child_interrupted_before_the_engine_took_it_is_requeued_once(team, sessions):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    ids = {}
    async def first(run):
        out = await rt.team_jobs.enqueue(run, {'agent_id': one.id, 'brief': 'Never started'}, reply='resume')
        ids['job'] = out.id
    lead_script(rt, engine, thread, first)
    # Hold the child: admit nothing until we have staged the interruption.
    original = rt.team_jobs.dispatch_queued
    async def hold():
        return None
    rt.team_jobs.dispatch_queued = hold
    await rt.send_turn(thread.id, 'go')
    await wait_until(lambda: 'job' in ids)
    await wait_until(lambda: not rt._runs)
    async with sessions() as db:
        row = await db.get(AgentJob, ids['job'])
        turn = await thread_store.start_turn(db, (await rt.create_thread(agent_id=one.id, kind='job', job_id=row.id)).id, 'x', owner_instance='gone')
        await db.execute(update(AgentJob).where(AgentJob.id == row.id).values(status='running', current_turn_id=turn.id, assignee_turn_id=turn.id, assignee_thread_id=turn.thread_id))
        await db.commit()
        await thread_store._interrupt(db, turn.id, reason='lease_expired')
    rt.team_jobs.dispatch_queued = original
    await rt.team_jobs.reconcile()
    async with sessions() as db:
        row = await db.get(AgentJob, ids['job'])
        assert (row.status, row.restarts, row.restart) == ('queued', 1, 'rerun')
    await rt.team_jobs.tick()
    async def finished():
        async with sessions() as db:
            return (await db.get(AgentJob, ids['job'])).status == 'completed'
    await wait_until(finished)


async def test_job_status_refuses_this_turn_and_blocking_jobs_cannot_delegate(team, sessions):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    seen = {}
    async def nested(ctx):
        out = await rt.team_jobs.delegate(rt._runs[ctx.turn_id], {'agent_ids': [two.id], 'brief': 'deeper'})
        seen['nested'] = {'code': out.get('code')}
    engine.nested = nested
    async def first(run):
        out = await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'Do it'})
        job_id = out['receipts'][0]['job_id']
        try:
            await rt.team_jobs.job_status(run, {'job_id': job_id})
        except service.TeamError as exc:
            seen['code'] = exc.code
        seen['listing'] = await rt.team_jobs.job_status(run, {})
    lead_script(rt, engine, thread, first)
    await rt.send_turn(thread.id, 'go')
    await wait_until(lambda: 'listing' in seen)
    assert seen['code'] == 'same_turn' and seen['listing']['jobs'] == []

    thread2 = await rt.create_thread(agent_id=lead.id)
    async def ask_blocking(ctx, sink, gate):
        seen['ask'] = await rt.team_jobs.ask(rt._runs[ctx.turn_id], {'agent_id': one.id, 'brief': 'NESTED please'})
        return TurnOutcome('succeeded', result='ok')
    engine.handlers[thread2.id] = ask_blocking
    await rt.send_turn(thread2.id, 'go')
    await wait_until(lambda: 'ask' in seen)
    assert seen['ask']['status'] == 'completed' and seen['nested']['code'] == 'use_ask'


async def test_person_message_gets_a_note_about_outstanding_work(team, sessions):
    rt, engine, lead, one, two = team
    thread = await rt.create_thread(agent_id=lead.id)
    async def first(run):
        await rt.team_jobs.delegate(run, {'agent_ids': [one.id], 'brief': 'BLOCK on it'})
    lead_script(rt, engine, thread, first)
    await rt.send_turn(thread.id, 'go')
    async def waiting():
        root = await root_of(sessions, thread.id)
        return root and root.status == 'waiting'
    await wait_until(waiting)
    note = await rt.team_jobs.outstanding_note(thread.id)
    assert 'Reviewer' in note and 'Do not assign it again' in note
    await rt.team_jobs.cancel((await root_of(sessions, thread.id)).id)
