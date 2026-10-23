"""Trust and correctness fixes for the team code: room origin, unattended
creation, lead lifecycle, cancelled asks and job events."""
import asyncio
import json
import pytest
from fastapi import FastAPI
import httpx
from sqlalchemy import select
from app.db.models import AgentJob, Event, Team, Agent
from app.services import agent_store
from app.services.provider_adapter import TurnOutcome
from app.team import policy, service
from app.routers.team import router as team_router, get_runtime
from tests.services.thread_harness import sessions, owner, cleanup, wait_until  # noqa: F401
from tests.team.test_collaboration import Engine, harness  # noqa: F401


@pytest.mark.parametrize('origin', ['routine', 'outside', 'webhook'])
@pytest.mark.parametrize('name', ['create_agent', 'create_room'])
def test_unattended_origins_cannot_create(origin, name):
    team = policy.TurnTeam(1, 1, True, True, origin=origin)
    assert policy.automatic('mcp__team__' + name, 'ask', team, {})[0] == 'denied'


def test_strictest_origin():
    assert policy.strictest(['person', 'routine']) == 'routine'
    assert policy.strictest(['person', 'outside', 'routine']) == 'outside'
    assert policy.strictest([]) == 'person'


async def test_room_member_turn_keeps_the_origin_of_what_it_read(harness, sessions):
    rt, engine, lead, worker = harness
    room = await rt.rooms.create({'name': 'Origin', 'member_ids': [lead.id, worker.id], 'default_responder_kind': 'mentions'})
    # A message an outside-origin lead posted wakes the reviewer.
    await rt.rooms.post(room['id'], '@Reviewer please look', author={'kind': 'agent', 'agent_id': lead.id, 'label': 'Lead'},
                        source={'kind': 'agent_tool', 'origin': 'outside'}, chain_depth=0, caller_id=lead.id)
    await rt.rooms.work(room['id'])
    assert engine.contexts[-1].team.origin == 'outside'
    rows = await rt.rooms.messages(room['id'])
    assert rows[-1]['source']['origin'] == 'outside'


async def test_person_message_keeps_person_origin(harness):
    rt, engine, lead, worker = harness
    room = await rt.rooms.create({'name': 'Plain', 'member_ids': [lead.id]})
    await rt.rooms.post(room['id'], 'hello')
    await rt.rooms.work(room['id'])
    assert engine.contexts[-1].team.origin == 'person'


async def test_say_in_room_records_origin(harness, sessions):
    rt, engine, lead, worker = harness
    room = await rt.rooms.create({'name': 'Say', 'member_ids': [lead.id, worker.id], 'default_responder_kind': 'mentions'})
    thread = await rt.create_thread(agent_id=lead.id)
    async def handler(ctx, sink, gate):
        await rt.rooms.tool(rt._runs[ctx.turn_id], 'say_in_room', {'room_id': room['id'], 'text': 'hello'})
        return TurnOutcome('succeeded', result='ok')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'go', team_origin='outside')
    await wait_until(lambda: turn.id not in rt._runs)
    rows = await rt.rooms.messages(room['id'])
    assert rows[-1]['source']['origin'] == 'outside'


async def test_lead_route_refuses_engine_without_team_tools(harness, sessions, owner):
    rt, engine, lead, worker = harness
    async with sessions() as db:
        other = await agent_store.create_agent(db, owner_id=owner, name='Codex lead', provider='codex')
    app = FastAPI()
    app.include_router(team_router)
    app.dependency_overrides[get_runtime] = lambda: rt
    from app.services.local_access_policy import require_local_access
    app.dependency_overrides[require_local_access] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        refused = await client.post('/team/lead', json={'agent_id': other.id})
        assert refused.status_code == 409 and refused.json()['detail']['error_code'] == 'lead_engine'
        ok = await client.post('/team/lead', json={'agent_id': lead.id})
        assert ok.status_code == 200 and ok.json()['team']['lead_agent_id'] == lead.id
        assert (await client.get('/team')).json()['lead_ready']['ok'] is True


async def test_deleting_or_archiving_the_lead_clears_the_team_pointer(harness, sessions, owner):
    rt, engine, lead, worker = harness
    async with sessions() as db:
        await agent_store.update_agent(db, lead.id, archived=True)
        team = await service.team_for(db, owner)
        assert team.lead_agent_id is None
        assert (await db.get(Agent, lead.id)).team_id is None
        assert (await db.get(Agent, worker.id)).team_id == team.id
    async with sessions() as db:
        await agent_store.update_agent(db, lead.id, archived=False)
        await service.set_lead(db, owner, worker.id)
        await agent_store.delete_agent(db, worker.id)
        assert (await service.team_for(db, owner)).lead_agent_id is None


async def test_editing_instructions_marks_them_reviewed(harness, sessions):
    rt, engine, lead, worker = harness
    async with sessions() as db:
        assert (await db.get(Agent, worker.id)).instructions_reviewed_at is None
        agent = await agent_store.update_agent(db, worker.id, instructions='Be brief')
        assert agent.instructions_reviewed_at is not None


async def test_cancelled_ask_settles_its_job_at_once(harness, sessions):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    async def handler(ctx, sink, gate):
        task = asyncio.create_task(rt.team_jobs.ask(rt._runs[ctx.turn_id], {'agent_id': worker.id, 'brief': 'BLOCK'}))
        async def child_running():
            async with sessions() as db:
                return await db.scalar(select(AgentJob).where(AgentJob.requester_turn_id == ctx.turn_id, AgentJob.assignee_turn_id.is_not(None)))
        await wait_until(child_running)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return TurnOutcome('succeeded', result='done')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'go')
    await wait_until(lambda: turn.id not in rt._runs)
    async with sessions() as db:
        job = await db.scalar(select(AgentJob).where(AgentJob.requester_turn_id == turn.id))
        assert job.status == 'cancelled'
    await wait_until(lambda: not rt._runs)


async def test_job_update_event_is_emitted_on_needs_input(harness, sessions):
    rt, engine, lead, worker = harness
    from app.services import thread_store
    thread = await rt.create_thread(agent_id=lead.id)
    seen = {}
    async def handler(ctx, sink, gate):
        run = rt._runs[ctx.turn_id]
        task = asyncio.create_task(rt.team_jobs.ask(run, {'agent_id': worker.id, 'brief': 'BLOCK'}))
        async def child():
            async with sessions() as db:
                return await db.scalar(select(AgentJob).where(AgentJob.requester_turn_id == ctx.turn_id, AgentJob.assignee_turn_id.is_not(None)))
        job = await wait_until(child)
        async with sessions() as db:
            await thread_store.create_approval(db, thread_id=job.assignee_thread_id, turn_id=job.assignee_turn_id, request_id='r', tool='Write', input={}, summary='s', kind='other', owner_token=(await db.get(__import__('app.db.models', fromlist=['Turn']).Turn, job.assignee_turn_id)).owner_token)
        async def updated():
            async with sessions() as db:
                return await db.scalar(select(Event).where(Event.thread_id == thread.id, Event.type == 'team.job.updated'))
        seen['event'] = await wait_until(updated)
        await rt.team_jobs.cancel(job.id)
        await asyncio.gather(task, return_exceptions=True)
        return TurnOutcome('succeeded', result='done')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'go')
    await wait_until(lambda: turn.id not in rt._runs)
    assert seen['event'].data['status'] == 'needs_input'


def test_real_mcp_stdio_server_lists_tools_and_relays_through_the_mailbox(tmp_path):
    import subprocess, sys, threading, time
    from pathlib import Path
    backend = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen([sys.executable, '-m', 'app.team.mcp_server', '--mailbox', str(tmp_path)], cwd=backend,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    def answer():
        for _ in range(100):
            for path in tmp_path.glob('req-*.json'):
                request = json.loads(path.read_text())
                (tmp_path / path.name.replace('req-', 'resp-')).write_text(json.dumps({'agents': [], 'echo': request['name']}))
                return
            time.sleep(.05)
    helper = threading.Thread(target=answer)
    helper.start()
    try:
        def rpc(message):
            proc.stdin.write(json.dumps(message) + '\n')
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())
        assert rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'})['result']['serverInfo']['name'] == 'team'
        names = {t['name'] for t in rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']}
        assert {'list_agents', 'ask_agent', 'create_agent'} <= names
        called = rpc({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'list_agents', 'arguments': {}}})
        assert json.loads(called['result']['content'][0]['text'])['echo'] == 'list_agents'
        unknown = rpc({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'nope', 'arguments': {}}})
        assert unknown['result']['isError'] is True
    finally:
        proc.terminate()
        proc.wait(5)
        helper.join()


async def test_recover_cancels_live_jobs_whose_requester_turn_ended(harness, sessions):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    ids = {}
    async def handler(ctx, sink, gate):
        job = await rt.team_jobs.enqueue(rt._runs[ctx.turn_id], {'agent_id': worker.id, 'brief': 'queued only'})
        ids['job'] = job.id
        return TurnOutcome('succeeded', result='ended without waiting')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'go')
    await wait_until(lambda: turn.id not in rt._runs)
    await rt.team_jobs.recover()
    async with sessions() as db:
        job = await db.get(AgentJob, ids['job'])
        assert (job.status, job.outcome) == ('failed', 'interrupted')

