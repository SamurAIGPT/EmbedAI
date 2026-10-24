"""Team setup plans: validation, receipts, scoped undo, deletion and housekeeping."""
import pytest
from sqlalchemy import select, func
from app.db.models import Agent, AgentJob, Thread, TeamEvent, Turn, TeamSetupRequest
from app.services import agent_store
from app.team import service, setup, policy
from tests.services.thread_harness import sessions, owner  # noqa: F401


def engine_check(provider, model):
    if provider != 'claude':
        raise service.TeamError('unknown_provider', f'Unknown engine: {provider}')
    if model == 'bad':
        raise service.TeamError('unknown_model', 'No such model.')


@pytest.fixture
async def team(sessions, owner):
    async with sessions() as db:
        lead = await agent_store.create_agent(db, owner_id=owner, name='Lead')
        worker = await agent_store.create_agent(db, owner_id=owner, name='Worker', instructions='Old')
        await service.set_lead(db, owner, lead.id)
        await service.change_member(db, owner, worker.id)
    return lead.id, worker.id


async def propose(sessions, owner, caller, plan, turn=1, **kw):
    async with sessions() as db:
        return await setup.propose(db, owner, caller, turn, 10, plan, check_engine=engine_check, **kw)


async def test_catalog_lists_team_engines_and_revisions(sessions, owner, team):
    lead, worker = team
    async with sessions() as db:
        data = await setup.catalog(db, owner, lead, engines=[{'id': 'claude', 'models': ['m1']}])
    assert {a['name'] for a in data['agents']} == {'Lead', 'Worker'}
    assert all(len(a['revision']) == 16 for a in data['agents'])
    assert data['limits']['max_operations'] == 8 and data['engines'][0]['id'] == 'claude'


async def test_create_and_update_apply_atomically_with_receipt(sessions, owner, team):
    lead, worker = team
    plan = {'operations': [{'op': 'create', 'name': 'Writer', 'title': 'Copy', 'instructions': 'Write well'},
                           {'op': 'update', 'agent_id': worker, 'fields': {'title': 'Reviewer'}}]}
    result = await propose(sessions, owner, lead, plan, request_id='r1')
    assert result['state'] == 'applied' and [r['action'] for r in result['results']] == ['created', 'updated']
    async with sessions() as db:
        writer = await db.get(Agent, result['results'][0]['agent_id'])
        assert writer.approval_mode == 'ask' and writer.created_by_agent_id == lead and writer.provider == 'claude'
        assert (await db.get(Agent, worker)).title == 'Reviewer'
    again = await propose(sessions, owner, lead, plan, request_id='r1')
    assert again['replayed'] is True and again['results'] == result['results']
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Agent).where(Agent.name == 'Writer')) == 1


async def test_invalid_plan_changes_nothing(sessions, owner, team):
    lead, worker = team
    plan = {'operations': [{'op': 'create', 'name': 'Good'}, {'op': 'update', 'agent_id': worker, 'fields': {'approval_mode': 'auto'}}]}
    with pytest.raises(service.TeamError) as exc:
        await propose(sessions, owner, lead, plan)
    assert exc.value.code == 'invalid_input'
    async with sessions() as db:
        assert not await db.scalar(select(Agent.id).where(Agent.name == 'Good'))


@pytest.mark.parametrize('operations,code', [
    ([], 'invalid_input'),
    ([{'op': 'create', 'name': 'X', 'provider': 'nope'}], 'unknown_provider'),
    ([{'op': 'create', 'name': 'X', 'model': 'bad'}], 'unknown_model'),
    ([{'op': 'create', 'name': 'Lead'}], 'name_taken'),
    ([{'op': 'create', 'name': 'A'}, {'op': 'create', 'name': 'A'}], 'name_taken'),
    ([{'op': 'create', 'name': f'N{i}'} for i in range(9)], 'invalid_input'),
    ([{'op': 'create', 'name': f'N{i}'} for i in range(5)], 'creation_limit'),
    ([{'op': 'create', 'name': 'X', 'approval_mode': 'plan'}], 'invalid_input'),
    ([{'op': 'delete', 'agent_id': 1}], 'invalid_input'),
])
async def test_validation_codes(sessions, owner, team, operations, code):
    with pytest.raises(service.TeamError) as exc:
        await propose(sessions, owner, team[0], {'operations': operations})
    assert exc.value.code == code


async def test_only_lead_may_propose_and_lead_engine_is_pinned(sessions, owner, team):
    lead, worker = team
    with pytest.raises(service.TeamError) as exc:
        await propose(sessions, owner, worker, {'operations': [{'op': 'create', 'name': 'X'}]})
    assert exc.value.code == 'lead_only'
    with pytest.raises(service.TeamError) as exc:
        await propose(sessions, owner, lead, {'operations': [{'op': 'update', 'agent_id': lead, 'fields': {'model': 'm'}}]})
    assert exc.value.code == 'invalid_input'


async def test_expected_revision_drift_is_refused(sessions, owner, team):
    lead, worker = team
    async with sessions() as db:
        before = (await setup.catalog(db, owner, lead, engines=[]))['agents']
    revision = next(a['revision'] for a in before if a['id'] == worker)
    async with sessions() as db:
        (await db.get(Agent, worker)).title = 'Edited by person'
        await db.commit()
    plan = {'operations': [{'op': 'update', 'agent_id': worker, 'fields': {'instructions': 'New'}, 'expected_revision': revision}]}
    with pytest.raises(service.TeamError) as exc:
        await propose(sessions, owner, lead, plan)
    assert exc.value.code == 'changed_since'


async def test_busy_target_is_refused(sessions, owner, team):
    lead, worker = team
    async with sessions() as db:
        thread = Thread(owner_id=owner, provider='claude', agent_id=worker)
        db.add(thread)
        await db.flush()
        db.add(Turn(thread_id=thread.id, seq=1, status='running', prompt_text='x', owner_token='t', owner_instance='t', engine_phase='preparing', origin='person'))
        await db.commit()
    with pytest.raises(service.TeamError) as exc:
        await propose(sessions, owner, lead, {'operations': [{'op': 'update', 'agent_id': worker, 'fields': {'title': 'T'}}]})
    assert exc.value.code == 'agent_busy'


async def test_undo_restores_and_refuses_when_stale_or_used(sessions, owner, team):
    lead, worker = team
    plan = {'operations': [{'op': 'create', 'name': 'Temp'}, {'op': 'update', 'agent_id': worker, 'fields': {'instructions': 'Changed'}}]}
    result = await propose(sessions, owner, lead, plan, request_id='u1')
    async with sessions() as db:
        out = await setup.undo(db, owner, 'u1')
    assert out['state'] == 'undone'
    async with sessions() as db:
        assert (await db.get(Agent, worker)).instructions == 'Old'
        assert not await db.scalar(select(Agent.id).where(Agent.name == 'Temp'))
    # Stale: someone edited the updated agent afterwards.
    await propose(sessions, owner, lead, {'operations': [{'op': 'update', 'agent_id': worker, 'fields': {'title': 'A'}}]}, request_id='u2')
    async with sessions() as db:
        (await db.get(Agent, worker)).title = 'B'
        await db.commit()
    async with sessions() as db:
        with pytest.raises(service.TeamError) as exc:
            await setup.undo(db, owner, 'u2')
    assert exc.value.code == 'changed_since'
    # Used: a created agent that already has a conversation is kept.
    made = await propose(sessions, owner, lead, {'operations': [{'op': 'create', 'name': 'Used'}]}, request_id='u3')
    async with sessions() as db:
        db.add(Thread(owner_id=owner, provider='claude', agent_id=made['results'][0]['agent_id']))
        await db.commit()
    async with sessions() as db:
        with pytest.raises(service.TeamError) as exc:
            await setup.undo(db, owner, 'u3')
    assert exc.value.code == 'agent_used'
    async with sessions() as db:
        with pytest.raises(service.TeamError) as exc:
            await setup.undo(db, owner, 'missing')
    assert exc.value.code == 'request_not_found'


async def test_deletion_rules(sessions, owner, team):
    lead, worker = team
    for target, code in ((lead, 'lead_required'), (999999, 'agent_not_found')):
        async with sessions() as db:
            with pytest.raises(service.TeamError) as exc:
                await setup.prepare_deletion(db, owner, lead, 1, 10, {'agent_id': target, 'reason': 'x'})
        assert exc.value.code == code
    async with sessions() as db:
        record = await setup.prepare_deletion(db, owner, lead, 1, 10, {'agent_id': worker, 'reason': 'Not needed'})
        assert record['agent_id'] == worker
    async with sessions() as db:
        db.add(AgentJob(owner_id=owner, requester_agent_id=lead, requester_thread_id=1, requester_turn_id=1, assignee_agent_id=worker, brief='b', brief_hash='h', status='running'))
        await db.commit()
    async with sessions() as db:
        with pytest.raises(service.TeamError) as exc:
            await setup.prepare_deletion(db, owner, lead, 1, 10, {'agent_id': worker, 'reason': 'x'})
    assert exc.value.code == 'agent_busy'


async def test_threads_listing_and_close_rules(sessions, owner, team):
    lead, worker = team
    async with sessions() as db:
        thread = Thread(owner_id=owner, provider='claude', agent_id=worker, kind='job', title='Review')
        db.add(thread)
        await db.flush()
        job = AgentJob(owner_id=owner, requester_agent_id=lead, requester_thread_id=1, requester_turn_id=1, assignee_agent_id=worker,
                       assignee_thread_id=thread.id, brief='b', brief_hash='h', status='completed')
        db.add(job)
        await db.commit()
        tid = thread.id
    async with sessions() as db:
        rows = await setup.list_threads(db, owner, lead)
    assert rows[0]['thread_id'] == tid and rows[0]['opened_by'] == 'Lead' and rows[0]['state'] == 'idle'
    async with sessions() as db:
        with pytest.raises(service.TeamError) as exc:
            await setup.close_thread(db, owner, worker, tid, current_thread_id=5)
    assert exc.value.code == 'not_allowed'
    async with sessions() as db:
        with pytest.raises(service.TeamError) as exc:
            await setup.close_thread(db, owner, lead, tid, current_thread_id=tid)
    assert exc.value.code == 'current_thread'
    async with sessions() as db:
        assert (await setup.close_thread(db, owner, lead, tid, current_thread_id=5))['state'] == 'closed'
        assert (await setup.close_thread(db, owner, lead, tid, current_thread_id=5))['already_closed'] is True
        assert (await db.get(Thread, tid)).archived_at is not None
    async with sessions() as db:
        rows = await setup.list_threads(db, owner, lead)
    assert rows[0]['state'] == 'closed'


async def test_close_refuses_running_thread(sessions, owner, team):
    lead, worker = team
    async with sessions() as db:
        thread = Thread(owner_id=owner, provider='claude', agent_id=worker, kind='job')
        db.add(thread)
        await db.flush()
        db.add(AgentJob(owner_id=owner, requester_agent_id=lead, requester_thread_id=1, requester_turn_id=1, assignee_agent_id=worker, assignee_thread_id=thread.id, brief='b', brief_hash='h', status='running'))
        await db.commit()
        tid = thread.id
    async with sessions() as db:
        with pytest.raises(service.TeamError) as exc:
            await setup.close_thread(db, owner, lead, tid, current_thread_id=5)
    assert exc.value.code == 'thread_busy'


def test_policy_for_setup_tools():
    lead = policy.TurnTeam(1, 1, True, True)
    member = policy.TurnTeam(1, 2, False, False)
    outside = policy.TurnTeam(1, 1, True, True, origin='outside')
    d = lambda tool, mode, team: (policy.automatic('mcp__team__' + tool, mode, team, {}) or (None,))[0]
    assert d('list_team_setup', 'ask', member) == 'allowed' and d('list_threads', 'ask', member) == 'allowed'
    assert d('propose_team_setup', 'ask', lead) == 'allowed'
    assert d('propose_team_setup', 'plan', lead) == 'denied' and d('propose_team_setup', 'ask', outside) == 'denied'
    assert d('propose_team_setup', 'ask', member) == 'denied'
    assert d('propose_bot_deletion', 'ask', lead) is None  # always a card
    assert d('propose_bot_deletion', 'ask', member) == 'denied' and d('propose_bot_deletion', 'plan', lead) == 'denied'
    assert d('close_thread', 'ask', member) == 'allowed' and d('close_thread', 'plan', member) == 'denied'


from app.services.provider_adapter import TurnOutcome  # noqa: E402
from tests.team.test_collaboration import harness  # noqa: E402,F401
from tests.services.thread_harness import wait_until  # noqa: E402


async def test_tools_apply_through_the_turn_and_record_events(harness, sessions, owner):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    seen = {}

    async def handler(ctx, sink, gate):
        run = rt._runs[ctx.turn_id]
        seen['setup'] = await setup.call(rt, run, 'list_team_setup', {})
        seen['made'] = await setup.call(rt, run, 'propose_team_setup', {'operations': [{'op': 'create', 'name': 'Scribe', 'instructions': 'Summarize files.'}]})
        seen['again'] = await setup.call(rt, run, 'propose_team_setup', {'operations': [{'op': 'create', 'name': 'Scribe', 'instructions': 'Summarize files.'}]})
        seen['deleted'] = await setup.call(rt, run, 'propose_bot_deletion', {'agent_id': worker.id, 'reason': 'Not needed'})
        try:
            await setup.call(rt, run, 'propose_bot_deletion', {'agent_id': lead.id, 'reason': 'x'})
        except service.TeamError as exc:
            seen['lead'] = exc.code
        return TurnOutcome('succeeded', result='done')

    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'go')
    await wait_until(lambda: turn.id not in rt._runs)
    assert seen['setup']['engines'][0]['id'] == 'claude'
    assert seen['made']['state'] == 'applied' and seen['again']['replayed'] is True
    assert seen['deleted']['results'][0]['action'] == 'deleted' and seen['lead'] == 'lead_required'
    async with sessions() as db:
        assert not await db.get(Agent, worker.id)
        assert await db.scalar(select(Agent.id).where(Agent.name == 'Scribe'))
        kinds = set((await db.execute(select(TeamEvent.kind))).scalars())
        assert {'agent_created', 'agent_deleted'} <= kinds


async def test_receipt_reuse_binds_plan_caller_and_source(sessions, owner, team):
    lead, worker = team
    plan = {'operations': [{'op': 'create', 'name': 'Once'}]}
    await propose(sessions, owner, lead, plan, request_id='bound')
    async with sessions() as db:
        with pytest.raises(service.TeamError) as exc:
            await setup.propose(db, owner, lead, 2, 11, plan, check_engine=engine_check, request_id='bound')
    assert exc.value.code == 'request_conflict'
    with pytest.raises(service.TeamError) as exc:
        await propose(sessions, owner, lead, {'operations': [{'op': 'create', 'name': 'Other'}]}, request_id='bound')
    assert exc.value.code == 'request_conflict'
    async with sessions() as db:
        await service.set_lead(db, owner, worker)
    with pytest.raises(service.TeamError) as exc:
        await propose(sessions, owner, worker, plan, request_id='bound')
    assert exc.value.code == 'request_conflict'


async def test_receipt_undo_readiness_changes_after_first_chat(sessions, owner, team):
    result = await propose(sessions, owner, team[0], {'operations': [{'op': 'create', 'name': 'Unused'}]}, request_id='status')
    async with sessions() as db:
        row = await setup.existing(db, owner, 'status')
        assert (await setup.undo_status(db, row))['available']
        db.add(Thread(owner_id=owner, provider='claude', agent_id=result['results'][0]['agent_id']))
        await db.commit()
        assert (await setup.undo_status(db, row))['available'] is False


async def test_stop_between_validation_and_commit_rolls_back_plan(sessions, owner, team):
    checks = 0
    async def authorize(db):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise service.TeamError('forbidden', 'Stopped before commit')
    async with sessions() as db:
        with pytest.raises(service.TeamError):
            await setup.propose(db, owner, team[0], 1, 10, {'operations': [{'op': 'create', 'name': 'Stopped'}]}, check_engine=engine_check, authorize=authorize)
    async with sessions() as db:
        assert not await db.scalar(select(Agent.id).where(Agent.owner_id == owner, Agent.name == 'Stopped'))
        assert not await db.scalar(select(TeamSetupRequest).where(TeamSetupRequest.owner_id == owner))


async def test_setup_plan_redacts_secrets_but_retries_match_original(sessions, owner, team):
    secret = 'sk-' + 'a' * 48
    plan = {'operations': [{'op': 'create', 'name': 'Sanitized', 'instructions': f'Summarize files. Key: {secret}'}]}
    result = await propose(sessions, owner, team[0], plan, request_id='sanitized')
    async with sessions() as db:
        row = await setup.existing(db, owner, 'sanitized')
        import json
        assert secret not in json.dumps(row.plan) and secret not in json.dumps(row.result)
    replay = await propose(sessions, owner, team[0], plan, request_id='sanitized')
    assert replay['replayed'] is True and replay['results'] == result['results']


async def test_undo_keeps_created_agent_promoted_to_lead(sessions, owner, team):
    made = await propose(sessions, owner, team[0], {'operations': [{'op': 'create', 'name': 'Promoted'}]}, request_id='promoted')
    async with sessions() as db:
        await service.set_lead(db, owner, made['results'][0]['agent_id'])
        row = await setup.existing(db, owner, 'promoted')
        assert (await setup.undo_status(db, row))['available'] is False
        with pytest.raises(service.TeamError) as exc:
            await setup.undo(db, owner, 'promoted')
        assert exc.value.code == 'changed_since'
