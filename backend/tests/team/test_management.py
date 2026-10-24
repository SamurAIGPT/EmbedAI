"""Configuration tools reuse persisted settings, revisions and turn authority."""
import pytest
from sqlalchemy import select
from app.db.models import Agent, Routine, TeamSetupRequest
from app.services.provider_adapter import TurnOutcome
from app.team import management, policy, setup, service
from tests.team.test_collaboration import harness  # noqa: F401
from tests.services.thread_harness import sessions, owner, cleanup, wait_until  # noqa: F401


async def run_tools(harness, operation):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    seen = {}
    async def handler(ctx, sink, gate):
        seen.update(await operation(rt, rt._runs[ctx.turn_id], worker))
        return TurnOutcome('succeeded', result='done')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'Configure my agent')
    await wait_until(lambda: turn.id not in rt._runs)
    return seen


async def test_create_preview_resume_and_retry_one_routine(harness, sessions, owner):
    async def operation(rt, run, worker):
        preview = await management.call(rt, run, 'preview_schedule', {'schedule_kind': 'weekdays', 'schedule_spec': {'days': [1,2,3,4,5], 'time': '09:00'}, 'timezone': 'Asia/Kolkata'})
        args = {'agent_id': worker.id, 'name': 'Daily file brief', 'prompt': 'Summarize the files.', **{key: preview[key] for key in ('schedule_kind', 'schedule_spec', 'timezone')}, 'request_id': 'daily'}
        made = await management.call(rt, run, 'create_routine', args)
        again = await management.call(rt, run, 'create_routine', args)
        revision = made['routine']['revision']
        resumed = await management.call(rt, run, 'manage_routine', {'agent_id': worker.id, 'routine_id': made['routine']['id'], 'action': 'resume', 'expected_revision': revision})
        return {'made': made, 'again': again, 'resumed': resumed}
    seen = await run_tools(harness, operation)
    assert seen['made']['routine']['paused'] is True
    assert seen['made']['routine']['next_dates']
    assert seen['again']['replayed'] is True
    assert seen['resumed']['routine']['paused'] is False
    async with sessions() as db:
        rows = list((await db.execute(select(Routine).where(Routine.owner_id == owner))).scalars())
        assert len(rows) == 1 and rows[0].approval_mode == 'ask' and rows[0].next_run_at is not None


async def test_profile_edits_same_agent_and_undo_preserves_permissions(harness, sessions, owner):
    async def operation(rt, run, worker):
        async with sessions() as db:
            revision = setup.revision(await db.get(Agent, worker.id))
        result = await management.call(rt, run, 'propose_profile', {'agent_id': worker.id, 'expected_revision': revision, 'fields': {'instructions': 'Summarize Product Hunt launches.'}})
        async with sessions() as db:
            worker = await db.get(Agent, worker.id)
            assert worker.instructions == 'Summarize Product Hunt launches.' and worker.approval_mode == 'ask'
        return result
    seen = await run_tools(harness, operation)
    assert seen['state'] == 'applied' and seen['results'][0]['changes'][0]['field'] == 'instructions'
    async with sessions() as db:
        assert (await setup.undo(db, owner, seen['request_id']))['state'] == 'undone'


async def test_nonlead_cannot_manage_another_agent(harness, sessions, owner):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=worker.id)
    seen = []
    async def handler(ctx, sink, gate):
        for tool, args in [('list_routines', {'agent_id': lead.id}), ('skills_list', {'agent_id': lead.id})]:
            with pytest.raises(service.TeamError) as exc:
                await management.call(rt, rt._runs[ctx.turn_id], tool, args)
            seen.append(exc.value.code)
        return TurnOutcome('succeeded')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'Read lead config')
    await wait_until(lambda: turn.id not in rt._runs)
    assert seen == ['lead_only', 'lead_only']


@pytest.mark.parametrize('tool', management.WRITES)
@pytest.mark.parametrize('origin,mode', [('outside','ask'), ('webhook','ask'), ('routine','ask'), ('harness','ask'), ('person','plan')])
def test_management_mutation_floor(tool, origin, mode):
    decision = policy.automatic('mcp__team__' + tool, mode, policy.TurnTeam(1, 1, True, True, origin=origin), {})
    assert decision[0] == 'denied'


async def test_restricted_chat_scope_stays_restricted_in_job_origin(harness, sessions, owner):
    rt, engine, lead, worker = harness
    thread = await rt.create_thread(agent_id=lead.id)
    seen = []
    async def handler(ctx, sink, gate):
        assert ctx.team.origin == 'restricted' and ctx.team.configuration_allowed is False
        with pytest.raises(service.TeamError) as exc:
            await management.call(rt, rt._runs[ctx.turn_id], 'create_routine', {'name': 'No', 'prompt': 'No'})
        seen.append(exc.value.code)
        return TurnOutcome('succeeded')
    engine.handlers[thread.id] = handler
    turn = await rt.send_turn(thread.id, 'Configure something', configuration_allowed=False)
    await wait_until(lambda: turn.id not in rt._runs)
    assert seen == ['forbidden']


async def test_skill_authoring_is_disabled_and_scoped(harness, sessions, owner, monkeypatch, tmp_path):
    from app.services import agent_memory, agent_skills
    monkeypatch.setattr(agent_memory, 'agent_home', lambda agent_id: tmp_path / str(agent_id))
    monkeypatch.setattr(agent_skills, 'agent_home', lambda agent_id: tmp_path / str(agent_id))
    async def operation(rt, run, worker):
        args = {'agent_id': worker.id, 'action': 'create', 'name': 'file-brief', 'description': 'Summarize local files', 'body': 'Read only the provided files and cite each finding.', 'request_id': 'skill-once'}
        saved = await management.call(rt, run, 'skill_manage', args)
        replay = await management.call(rt, run, 'skill_manage', args)
        listed = await management.call(rt, run, 'skills_list', {'agent_id': worker.id})
        read = await management.call(rt, run, 'skills_list', {'agent_id': worker.id, 'name': 'file-brief'})
        with pytest.raises(service.TeamError) as exc:
            await management.call(rt, run, 'skill_manage', {**args, 'request_id': 'duplicate'})
        return {'saved': saved, 'replay': replay, 'listed': listed, 'read': read, 'code': exc.value.code}
    seen = await run_tools(harness, operation)
    assert seen['saved']['skill']['enabled'] is False
    assert seen['replay']['replayed'] is True and len(seen['listed']['skills']) == 1
    assert seen['code'] == 'name_taken' and 'cite each finding' in seen['read']['body']


async def test_profile_rejects_privileged_fields_without_writing(harness, sessions, owner):
    async def operation(rt, run, worker):
        async with sessions() as db:
            revision = setup.revision(await db.get(Agent, worker.id))
        for field in ('approval_mode', 'make_lead', 'memory_enabled', 'delegation_enabled'):
            with pytest.raises(service.TeamError) as exc:
                await management.call(rt, run, 'propose_profile', {'agent_id': worker.id, 'expected_revision': revision, 'fields': {field: True}})
            assert exc.value.code == 'invalid_input'
        return {'checked': True}
    assert (await run_tools(harness, operation))['checked']
    async with sessions() as db:
        assert not await db.scalar(select(TeamSetupRequest).where(TeamSetupRequest.owner_id == owner))


async def test_routine_stale_change_and_invalid_activation_fields_fail(harness, sessions, owner):
    async def operation(rt, run, worker):
        saved = await management.call(rt, run, 'create_routine', {'agent_id': worker.id, 'name': 'Paused work', 'prompt': 'Read local files', 'schedule_kind': 'manual', 'timezone': 'UTC'})
        row = saved['routine']
        with pytest.raises(service.TeamError) as exc:
            await management.call(rt, run, 'manage_routine', {'agent_id': worker.id, 'routine_id': row['id'], 'expected_revision': 'outdated', 'action': 'resume'})
        assert exc.value.code == 'changed_since'
        with pytest.raises(service.TeamError) as exc:
            await management.call(rt, run, 'create_routine', {'agent_id': worker.id, 'name': 'No automatic activation', 'prompt': 'x', 'paused': False})
        assert exc.value.code == 'invalid_input'
        return {'id': row['id']}
    seen = await run_tools(harness, operation)
    async with sessions() as db:
        assert (await db.get(Routine, seen['id'])).paused is True
