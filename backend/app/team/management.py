"""Chat configuration over existing agent, routine and skill services."""
from datetime import UTC, datetime
from pathlib import Path
from pydantic import ValidationError
from app.db.models import Agent, TeamSetupRequest, Routine
from app.db.transactions import begin_write
from app.schemas.agent import AgentUpdateRequest
from app.schemas.routine import RoutineCreateRequest, RoutineUpdateRequest
from app.services import agent_memory, agent_skills, routine_schedule, routine_service
from app.services.workspaces import require_workspace
from app.team import fence, policy, service, setup

READS = ('list_management_capabilities', 'list_routines', 'preview_schedule', 'skills_list')
WRITES = ('propose_profile', 'propose_model', 'create_routine', 'manage_routine', 'skill_manage')
TOOLS = READS + WRITES
ROUTINE_FIELDS = {'name', 'prompt', 'schedule_kind', 'schedule_spec', 'timezone'}
PROFILE_FIELDS = {'name', 'title', 'description', 'instructions', 'workspace_path'}


def strict(args, allowed):
    if not isinstance(args, dict) or set(args) - allowed:
        raise service.TeamError('invalid_input', 'Unrecognised settings for this operation.')


async def target(db, rt, run, agent_id=None):
    caller = await service.member(db, rt.owner_id, run.agent_id, team_id=run.ctx.team.team_id)
    team = await service.team_for(db, rt.owner_id, lock=True)
    agent_id = run.agent_id if agent_id is None else agent_id
    if type(agent_id) is not int:
        raise service.TeamError('invalid_input', 'Use an agent_id from list_team_setup.')
    if agent_id != caller.id and team.lead_agent_id != caller.id:
        raise service.TeamError('lead_only', 'Only the current lead can configure another agent.')
    await db.get(Agent, agent_id, with_for_update=True, populate_existing=True)
    return await service.member(db, rt.owner_id, agent_id, team_id=team.id), team


def routine_info(row):
    now = datetime.now(UTC)
    return {'id': row.id, 'agent_id': row.agent_id, 'name': fence.line(row.name),
            'prompt': agent_memory.redact_secrets(row.prompt)[0], 'schedule_kind': row.schedule_kind,
            'schedule_spec': row.schedule_spec, 'timezone': row.timezone, 'paused': row.paused,
            'next_run_at': row.next_run_at.isoformat() if row.next_run_at else None,
            'next_dates': [date.isoformat() for date in routine_schedule.next_dates(row.schedule_kind, row.schedule_spec, row.timezone, now)],
            'revision': setup.digest({key: getattr(row, key) for key in sorted(ROUTINE_FIELDS | {'paused', 'agent_id', 'updated_at'})})[:16]}


def capabilities():
    return {'available': ['create and edit team agents', 'profile and default model changes', 'delegation and follow-up',
                          'rooms and job-thread housekeeping', 'routine schedules, pause, resume and run now', 'saved skill authoring', 'conversation search'],
            'requires_settings': {'connections': 'App connections need a separate integration; chat cannot provision credentials or app-event subscriptions.',
                                  'computers': 'Allocate or remove a computer in Agent settings. Computer actions use existing approved tools.',
                                  'lead_role': 'Only the person chooses the team lead and coordinated agents in Agent settings.'},
            'unavailable': ['app-event and limit-reset triggers', 'voice settings and voice notes', 'shared team memory catalog', 'arbitrary MCP server installation'],
            'routine_note': 'Chat-created routines start paused. Resume is a separate explicit, approval-gated action.'}


async def call(rt, run, name, args):
    try:
        return await _call(rt, run, name, args)
    except (ValidationError, ValueError) as exc:
        if isinstance(exc, service.TeamError):
            raise
        raise service.TeamError('invalid_input', str(exc)[:1000]) from None
    except routine_service.RoutineNotFound:
        raise service.TeamError('routine_not_found', 'No routine with that id is available.') from None
    except routine_service.InvalidRoutine as exc:
        raise service.TeamError(exc.code, str(exc)) from None
    except routine_service.RoutineBusy:
        raise service.TeamError('routine_busy', 'That routine already has an active run.') from None
    except agent_skills.SkillError as exc:
        raise service.TeamError(exc.code, exc.message) from None


async def _call(rt, run, name, args):
    writing = name in WRITES
    if writing and (run.ctx.approval_mode == 'plan' or run.ctx.team.origin in policy.UNATTENDED):
        raise service.TeamError('forbidden', 'Configuration changes are unavailable for this turn.')
    if name == 'list_management_capabilities':
        strict(args, set())
        return capabilities()
    if name == 'preview_schedule':
        strict(args, {'schedule_kind', 'schedule_spec', 'timezone'})
        now = datetime.now(UTC)
        kind, zone = args.get('schedule_kind', 'manual'), args.get('timezone', 'UTC')
        spec = routine_schedule.normalize(kind, args.get('schedule_spec', {}), zone, now=now)
        return {'schedule_kind': kind, 'schedule_spec': spec, 'timezone': zone, 'now': now.isoformat(),
                'summary': routine_schedule.summary(kind, spec, zone),
                'next_dates': [date.isoformat() for date in routine_schedule.next_dates(kind, spec, zone, now)]}
    async with rt.session() as db:
        await begin_write(db)
        if writing:
            await setup._authorizer(rt, run)(db)
        agent, team = await target(db, rt, run, args.get('agent_id'))
        if name == 'list_routines':
            strict(args, {'agent_id'})
            return {'now': datetime.now(UTC).isoformat(), 'routines': [routine_info(row) for row in await routine_service.list_routines(db, rt.owner_id) if row.agent_id == agent.id]}
        if name == 'skills_list':
            strict(args, {'agent_id', 'name'})
            if args.get('name') is not None:
                item = agent_skills.read_skill(agent.id, agent_skills.check_name(args['name']))
                return {'name': item.name, 'description': item.description, 'body': item.body, 'enabled': item.enabled,
                        'revision': setup.digest([item.description, item.body, item.enabled])[:16]}
            return {'agent_id': agent.id, 'skills': [{'name': item.name, 'description': fence.line(item.description), 'enabled': item.enabled,
                       'revision': setup.digest([item.description, item.body, item.enabled])[:16]} for item in agent_skills.list_skills(agent.id)]}
        # Stable, source-bound receipts cover creates as well as follow-up configuration.
        request_id = args.get('request_id') or setup.digest([run.turn_id, name, args])[:32]
        if not isinstance(request_id, str) or not 0 < len(request_id) <= 64:
            raise service.TeamError('invalid_input', 'request_id must be at most 64 characters.')
        binding = setup.digest([name, args])
        previous = await setup.existing(db, rt.owner_id, request_id)
        if previous:
            if previous.lead_agent_id != run.agent_id or previous.thread_id != run.thread_id or previous.plan.get('_request_digest') != binding:
                raise service.TeamError('request_conflict', 'This request_id belongs to another change or conversation.')
            result = setup.replay(previous)
            if previous.kind == 'setup' and result['state'] == 'applied':
                await run.recorder.emit('team.setup.applied', {**result, 'undo_available': (await setup.undo_status(db, previous))['available']})
            return result
        if await setup.busy(db, agent.id, except_thread=run.thread_id):
            raise service.TeamError('agent_busy', 'That agent is working. Try again when it is idle.')
        undo = {}
        receipt_kind = name
        if name in ('propose_profile', 'propose_model'):
            strict(args, {'agent_id', 'fields', 'expected_revision', 'request_id'})
            fields = args.get('fields')
            allowed = PROFILE_FIELDS if name == 'propose_profile' else {'provider', 'model'}
            if not isinstance(fields, dict) or not fields or set(fields) - allowed:
                raise service.TeamError('invalid_input', 'Supply only the profile or default-model fields this tool supports.')
            if args.get('expected_revision') != setup.revision(agent):
                raise service.TeamError('changed_since', 'Read list_team_setup and use this agent\'s current revision.')
            if any(value is None and key not in ('title', 'description', 'model', 'workspace_path') for key, value in fields.items()):
                raise service.TeamError('invalid_input', 'That setting cannot be null.')
            fields = AgentUpdateRequest.model_validate(fields).model_dump(exclude_unset=True)
            fields = setup.redacted(fields)
            before = {key: getattr(agent, key) for key in fields}
            if 'workspace_path' in fields and fields['workspace_path']:
                folder = Path(fields['workspace_path']).expanduser()
                if not folder.is_absolute():
                    raise service.TeamError('workspace_unavailable', 'Choose an existing absolute working folder.')
                try:
                    fields['workspace_path'] = str(require_workspace(folder.resolve()))
                except OSError:
                    raise service.TeamError('workspace_unavailable', 'The working folder does not exist or cannot be used.') from None
            provider = fields.get('provider', agent.provider)
            if 'provider' in fields and provider != agent.provider:
                fields.setdefault('model', None)
                before.setdefault('model', agent.model)
            if name == 'propose_model':
                setup._checker(rt)(provider, fields.get('model', agent.model))
                caps = rt.engines.capabilities(provider)
                if agent.id == team.lead_agent_id and not caps.mcp:
                    raise service.TeamError('lead_engine', 'The lead must keep an engine with team tools.')
                if not caps.computers and await rt.computers.for_agent(agent.id) is not None:
                    raise service.TeamError('engine_without_computers', 'Remove the agent\'s computer in settings before choosing this engine.')
            for field, value in fields.items():
                setattr(agent, field, value)
            agent.updated_at = service.now()
            await db.flush()
            result = {'results': [{'action': 'updated', 'agent_id': agent.id, 'name': agent.name, 'changes': setup.changes(before, fields)}],
                      'model_note': 'Existing conversations retain their model and working folder. Saved defaults apply to new conversations.'}
            undo = {'created': [], 'updated': [{'agent_id': agent.id, 'before': before, 'hash': setup.full_hash(agent)}]}
            receipt_kind = 'setup'
        elif name == 'create_routine':
            strict(args, {'agent_id', 'request_id'} | ROUTINE_FIELDS)
            data = RoutineCreateRequest.model_validate({**{key: value for key, value in args.items() if key in ROUTINE_FIELDS}, 'agent_id': agent.id, 'paused': True, 'approval_mode': 'ask'})
            routine = await routine_service.create_routine(db, rt.owner_id, setup.redacted(data.model_dump()), commit=False)
            result = {'routine': routine_info(routine), 'note': 'Saved paused. Review it in Routines; resume separately when the person explicitly asks.'}
        elif name == 'manage_routine':
            strict(args, {'agent_id', 'request_id', 'routine_id', 'action', 'changes', 'expected_revision'})
            if type(args.get('routine_id')) is not int:
                raise service.TeamError('invalid_input', 'Choose a routine_id from list_routines.')
            routine = await db.get(Routine, args['routine_id'], with_for_update=True)
            if routine is None or routine.owner_id != rt.owner_id or routine.agent_id != agent.id:
                raise service.TeamError('routine_not_found', 'No routine with that id is available to this agent.')
            if args.get('expected_revision') != routine_info(routine)['revision']:
                raise service.TeamError('changed_since', 'Read list_routines and include its current revision.')
            action = args.get('action')
            if action != 'update' and args.get('changes'):
                raise service.TeamError('invalid_input', 'Only update accepts changes.')
            if action == 'update':
                fields = args.get('changes', {})
                strict(fields, ROUTINE_FIELDS)
                data = RoutineUpdateRequest.model_validate(fields).model_dump(exclude_unset=True)
                routine = await routine_service.update_routine(db, rt.owner_id, routine.id, setup.redacted(data), commit=False)
            elif action in ('pause', 'resume'):
                routine = await routine_service.set_paused(db, rt.owner_id, routine.id, action == 'pause', commit=False)
            elif action == 'run_now':
                queued = await routine_service.run_now(db, rt.owner_id, routine.id, commit=False)
                result = {'run_id': queued.id, 'status': queued.state, 'note': 'Queued, not completed.'}
            elif action == 'delete':
                await routine_service.delete_routine(db, rt.owner_id, routine.id, commit=False)
                result = {'routine_id': routine.id, 'action': 'deleted'}
            else:
                raise service.TeamError('invalid_input', 'Choose update, pause, resume, run_now or delete.')
            if action not in ('run_now', 'delete'):
                result = {'routine': routine_info(routine)}
        elif name == 'skill_manage':
            strict(args, {'agent_id', 'request_id', 'action', 'name', 'description', 'body', 'expected_revision'})
            skill_name = agent_skills.check_name(args.get('name'))
            found = next((item for item in agent_skills.list_skills(agent.id) if item.name == skill_name), None)
            if args.get('action') == 'create':
                if found:
                    raise service.TeamError('name_taken', 'That skill already exists. Use update with its revision.')
            elif args.get('action') == 'update':
                if found is None or args.get('expected_revision') != setup.digest([found.description, found.body, found.enabled])[:16]:
                    raise service.TeamError('changed_since', 'Read skills_list and include its current revision.')
            else:
                raise service.TeamError('invalid_input', 'Choose create or update.')
            await setup._authorizer(rt, run)(db)
            item = agent_skills.write_skill(agent.id, skill_name, args.get('description', ''), args.get('body', ''), enabled=False, source='agent')
            result = {'skill': {'name': item.name, 'description': item.description, 'enabled': False, 'warnings': item.warnings},
                      'note': 'Saved disabled. Review and enable in Agent settings; skill text grants no permissions.'}
        else:
            raise service.TeamError('unknown_tool', 'Unknown configuration tool.')
        await setup._authorizer(rt, run)(db)
        service.audit(db, team, name, agent_id=agent.id, thread_id=run.thread_id, data={'request_id': request_id})
        db.add(TeamSetupRequest(owner_id=rt.owner_id, team_id=team.id, lead_agent_id=run.agent_id,
                               thread_id=run.thread_id, turn_id=run.turn_id, request_id=request_id, kind=receipt_kind,
                               plan={'_request_digest': binding}, state='applied', result=result, undo=undo, decided_at=service.now()))
        await db.commit()
    response = {**result, 'request_id': request_id, 'state': 'applied', 'replayed': False}
    if receipt_kind == 'setup':
        await run.recorder.emit('team.setup.applied', {**response, 'undo_available': True})
    return response
