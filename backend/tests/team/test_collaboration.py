"""Durable collaboration with ordinary turns, real database transactions and gates."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from app.db.models import Agent, AgentJob, AgentJobEvent, TeamEvent, Approval, Thread, Turn, RoomDispatch, RoomMember
from app.services import agent_store, thread_store
from app.services.thread_runtime import ThreadRuntime
from app.services.thread_hub import ThreadHub
from app.services.provider_adapter import Capabilities, ProviderStatus, TurnOutcome, ALLOW
from app.team import service, policy
from app.team.bridge import TeamBridge, consume_allowed_approval
from app.rooms.mentions import parse
from tests.services.thread_harness import sessions, owner, cleanup, wait_until


class Engine:
    key = 'claude'
    capabilities = Capabilities(tools=True, approvals=True, mcp=True)
    custom_models = True
    def __init__(self):
        self.handlers = {}
        self.contexts = []
    def new_session_id(self):
        return None
    async def status(self):
        return ProviderStatus(ready=True)
    async def run_turn(self, ctx, sink, gate):
        self.contexts.append(ctx)
        if ctx.thread_id in self.handlers:
            return await self.handlers[ctx.thread_id](ctx, sink, gate)
        if 'BLOCK' in ctx.prompt:
            while not ctx.aborted:
                await asyncio.sleep(.03)
            return TurnOutcome('cancelled')
        await sink.emit('assistant.message', {'text': '(pass)' if 'PASS' in ctx.prompt else 'Specialist result'})
        return TurnOutcome('succeeded', result='(pass)' if 'PASS' in ctx.prompt else 'Specialist result', usage={'input_tokens': 3})


@pytest.fixture
async def harness(sessions, owner, monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import local_claude_runner
    monkeypatch.setattr(local_claude_runner, 'workspace_dir', lambda: tmp_path)
    monkeypatch.setattr(settings, 'TEAM_ENABLED', True)
    engine = Engine()
    runtime = ThreadRuntime(hub=ThreadHub(), adapters={'claude': engine}, owner_id=owner, _session_factory=sessions)
    async with sessions() as db:
        lead = await agent_store.create_agent(db, owner_id=owner, name='Lead')
        worker = await agent_store.create_agent(db, owner_id=owner, name='Reviewer', approval_mode='ask')
        await service.set_lead(db, owner, lead.id)
        await service.change_member(db, owner, worker.id)
    yield runtime, engine, lead, worker
    await runtime.shutdown()
    for task in runtime.rooms.tasks.values():
        if not task.done():
            task.cancel()
    await asyncio.gather(*runtime.rooms.tasks.values(), return_exceptions=True)


async def test_lead_idempotence_and_existing_agents_untouched(harness, sessions, owner):
    rt, engine, lead, worker = harness
    async with sessions() as db:
        first = await service.set_lead(db, owner, lead.id)
        second = await service.set_lead(db, owner, lead.id)
        assert first.id == second.id
        assert await db.scalar(select(func.count()).select_from(TeamEvent).where(TeamEvent.kind == 'lead_set', TeamEvent.team_id == first.id)) == 1
        unrelated = await agent_store.create_agent(db, owner_id=owner, name='Unrelated')
        assert unrelated.team_id is None
        with pytest.raises(service.TeamError, match='another lead'):
            await service.change_member(db, owner, lead.id, remove=True)


@pytest.mark.parametrize('origin,mode,name,args,expected', [
    ('person','plan','create_agent',{},'denied'), ('outside','ask','create_agent',{},'denied'),
    ('outside','ask','create_room',{},'denied'), ('outside','ask','ask_agent',{'mode':'work'},None),
    ('person','plan','ask_agent',{'mode':'consult'},'allowed'), ('person','plan','ask_agent',{},'denied'),
    ('person','ask','create_agent',{},'allowed'), ('person','ask','list_agents',{},'allowed'),
])
def test_team_policy_floor(origin, mode, name, args, expected):
    team = policy.TurnTeam(1, 1, True, True, origin=origin)
    decision = policy.automatic('mcp__team__'+name, mode, team, args)
    assert (decision[0] if decision else None) == expected


async def test_create_caps_defaults_and_workspace_safety(harness, sessions, owner, monkeypatch):
    rt, engine, lead, worker = harness
    async with sessions() as db:
        with pytest.raises(service.TeamError, match='project folder'):
            await service.create_specialist(db, owner, lead.id, 900, {'name':'Shared', 'share_workspace':True})
        await db.rollback()
        for i in range(4):
            agent = await service.create_specialist(db, owner, lead.id, 900, {'name':f'New {i}', 'instructions':'Do useful work'})
            assert agent.approval_mode == 'ask' and agent.workspace_path is None and agent.created_by_agent_id == lead.id
            agent_id = agent.id
        with pytest.raises(service.TeamError, match='creation limit'):
            await service.create_specialist(db, owner, lead.id, 900, {'name':'Fifth'})
        await db.rollback()
        assert await service.undo_create(db, owner, agent_id) == 'deleted'


async def test_one_use_approval_exact_arguments_and_turn_ownership(harness, sessions, owner):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    async with sessions() as db:
        turn = await thread_store.start_turn(db, thread.id, 'test', owner_instance='test')
        approval = await thread_store.create_approval(db, thread_id=thread.id, turn_id=turn.id, request_id='req', tool='mcp__team__list_agents', input={}, summary='List teammates', kind='other', owner_token=turn.owner_token)
        await thread_store.decide_approval(db, approval.id, 'allowed')
        turn_id, token, tool = turn.id, turn.owner_token, approval.tool
        assert not await consume_allowed_approval(db, turn_id, 'wrong', tool, {})
        assert not await consume_allowed_approval(db, turn_id, token, tool, {'extra':1})
    async def take():
        async with sessions() as db:
            return await consume_allowed_approval(db, turn_id, token, tool, {})
    results = await asyncio.gather(take(), take())
    assert sorted(results) == [False, True]


async def test_forged_mailbox_without_approval_cannot_create(harness, sessions):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    captured = {}
    async def handler(ctx, sink, gate):
        run = rt._runs[ctx.turn_id]
        bridge = TeamBridge(rt, run)
        path = bridge.folder / 'req-forged.working'
        path.write_text(json.dumps({'name':'create_agent','arguments':{'name':'Forged'}}))
        await bridge.handle(path)
        captured.update(json.loads((bridge.folder/'resp-forged.json').read_text()))
        await bridge.close()
        return TurnOutcome('succeeded', result='done')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'go')
    await wait_until(lambda: turn.id not in rt._runs)
    assert captured['code'] == 'not_approved'
    async with sessions() as db:
        assert not await db.scalar(select(Agent.id).where(Agent.owner_id == rt.owner_id, Agent.name == 'Forged'))


async def test_delegation_runs_child_stricter_mode_and_settles_once(harness, sessions):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    reports = []
    async def handler(ctx, sink, gate):
        args = {'agent_id': worker.id, 'brief':'Review the patch', 'mode':'consult'}
        run = rt._runs[ctx.turn_id]
        reports.append(await rt.team_jobs.ask(run, args))
        reports.append(await rt.team_jobs.ask(run, args))
        return TurnOutcome('succeeded', result='combined')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'review', approval_mode='accept_edits')
    await wait_until(lambda: turn.id not in rt._runs)
    assert [r['status'] for r in reports] == ['completed', 'completed']
    assert reports[0]['job_id'] == reports[1]['job_id']
    async with sessions() as db:
        job = await db.get(AgentJob, reports[0]['job_id'])
        assert job.approval_mode == 'plan'
        child = await db.get(Thread, job.assignee_thread_id)
        assert child.kind == 'job' and child.agent_id == worker.id
        assert await db.scalar(select(func.count()).select_from(AgentJobEvent).where(AgentJobEvent.job_id == job.id, AgentJobEvent.kind == 'settled')) == 1
    assert any(c.approval_mode == 'plan' for c in engine.contexts)


async def test_stop_cascades_and_drops_cancelled_result(harness, sessions):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    async def handler(ctx, sink, gate):
        await rt.team_jobs.ask(rt._runs[ctx.turn_id], {'agent_id':worker.id, 'brief':'BLOCK'})
        return TurnOutcome('cancelled')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'delegate')
    async def child_running():
        async with sessions() as db:
            return await db.scalar(select(AgentJob).where(AgentJob.requester_turn_id == turn.id, AgentJob.assignee_turn_id.is_not(None)))
    child = await wait_until(child_running)
    await rt.stop_turn(thread.id)
    await wait_until(lambda: not rt._runs)
    async with sessions() as db:
        job = await db.get(AgentJob, child.id)
        assert (job.status, job.outcome, job.result) == ('cancelled', 'stopped', '')


async def test_self_and_removed_member_refused(harness, sessions):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    results = []
    async def handler(ctx, sink, gate):
        run = rt._runs[ctx.turn_id]
        results.append(await rt.team_jobs.ask(run, {'agent_id':lead.id, 'brief':'loop'}))
        async with sessions() as db:
            await service.change_member(db, rt.owner_id, worker.id, remove=True)
        results.append(await rt.team_jobs.ask(run, {'agent_id':worker.id, 'brief':'outside'}))
        return TurnOutcome('succeeded')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'try')
    await wait_until(lambda: turn.id not in rt._runs)
    assert [r['code'] for r in results] == ['ancestor', 'unreachable_agent']


@pytest.mark.parametrize('text,expected', [('hi @Reviewer', [2]), ('a@Reviewer', []), ('/@Reviewer', []), ('`@Reviewer`', []), ('> @Reviewer\nhi @Lead', [1]), ('@Reviewer_x', []), ('@Reviewer!', [2]), ('@everyone @Lead', [None,1]), ('```\n@Lead\n```', [])])
def test_mentions(text, expected):
    assert parse(text, [(1,'Lead'), (2,'Reviewer')]) == expected


async def test_room_saved_message_queue_then_sequential_reply(harness, sessions):
    rt, engine, lead, worker = harness
    room = await rt.rooms.create({'name':'Review room', 'member_ids':[lead.id,worker.id], 'default_responder_kind':'everyone'})
    seq = await rt.rooms.post(room['id'], 'Please review')
    async with sessions() as db:
        dispatches = list((await db.execute(select(RoomDispatch).where(RoomDispatch.room_id == room['id']).order_by(RoomDispatch.id))).scalars())
        assert len(dispatches) == 2 and all(d.message_seq == seq for d in dispatches)
    await rt.rooms.work(room['id'])
    await rt.rooms.work(room['id'])
    rows = await rt.rooms.messages(room['id'])
    assert [r['author']['kind'] for r in rows] == ['person','agent','agent']
    assert rows[1]['source']['thread_id'] != rows[2]['source']['thread_id']
    # Second member sees the first reply, rather than a stale concurrent window.
    assert 'Specialist result' in engine.contexts[-1].prompt
    async with sessions() as db:
        chats = await thread_store.list_threads(db, rt.owner_id)
        assert not chats
        members = list((await db.execute(select(RoomMember).where(RoomMember.room_id == room['id']))).scalars())
        for m in members:
            assert (await db.get(Thread, m.member_thread_id)).kind == 'room_member'


async def test_room_hold_pass_and_stop_queued(harness, sessions):
    rt, engine, lead, worker = harness
    room = await rt.rooms.create({'name':'Quiet', 'member_ids':[lead.id,worker.id], 'default_responder_kind':'everyone'})
    await rt.rooms.hold(room['id'], worker.id, True)
    await rt.rooms.post(room['id'], 'PASS @Reviewer @Lead')
    await rt.rooms.work(room['id'])
    rows = await rt.rooms.messages(room['id'])
    assert len([r for r in rows if r.get('author')]) == 1
    await rt.rooms.hold(room['id'], worker.id, False)
    await rt.rooms.post(room['id'], 'later')
    assert await rt.rooms.stop(room['id']) == 2


async def test_room_member_threads_refuse_direct_send_and_hide_search(harness, sessions):
    from app.services.conversation_search import search
    rt, engine, lead, worker = harness
    room = await rt.rooms.create({'name':'Search', 'member_ids':[lead.id]})
    await rt.rooms.post(room['id'], 'Unique keyword')
    await rt.rooms.work(room['id'])
    room = await rt.rooms.detail(room['id'])
    with pytest.raises(Exception, match='in the room'):
        await rt.send_turn(room['members'][0]['member_thread_id'], 'bypass')
    async with sessions() as db:
        hits = await search(db, rt.owner_id, 'Unique keyword')
        assert len(hits) == 1 and hits[0].room_id == room['id']
