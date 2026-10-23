"""Team ownership, membership and model-created specialists.

Writers lock the team row, commit before returning and never copy private agent notes.
"""
from datetime import UTC, datetime
from sqlalchemy import select, func
from app.db.models import Agent, Thread, Team, TeamEvent, AgentJob
from app.db.transactions import begin_write
from app.services import agent_memory


def now():
    return datetime.now(UTC)


class TeamError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


async def team_for(db, owner_id, *, lock=False):
    query = select(Team).where(Team.owner_id == owner_id)
    return (await db.execute(query.with_for_update() if lock else query)).scalar_one_or_none()


def audit(db, team, kind, *, agent_id=None, thread_id=None, job_id=None, data=None):
    db.add(TeamEvent(owner_id=team.owner_id, team_id=team.id, kind=kind, agent_id=agent_id,
                     thread_id=thread_id, job_id=job_id, data=data or {}))


async def member(db, owner, agent_id, *, team_id=None):
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.owner_id != owner or agent.archived_at is not None:
        raise TeamError('agent_not_found', 'No live agent with that id.')
    if team_id is not None and agent.team_id != team_id:
        raise TeamError('unreachable_agent', 'That agent is outside this team.')
    return agent


async def set_lead(db, owner, agent_id, name='My team'):
    await begin_write(db)
    agent = await member(db, owner, agent_id)
    team = await team_for(db, owner, lock=True)
    if team is None:
        team = Team(owner_id=owner, name=name.strip() or 'My team')
        db.add(team)
        await db.flush()
    if agent.team_id is not None and agent.team_id != team.id:
        raise TeamError('other_team', 'This agent already belongs to another team.')
    if team.lead_agent_id != agent.id:
        team.lead_agent_id = agent.id
        team.updated_at = now()
        agent.team_id = team.id
        audit(db, team, 'lead_set', agent_id=agent.id)
    await db.commit()
    return team


async def change_member(db, owner, agent_id, *, remove=False, delegation_enabled=None):
    await begin_write(db)
    team = await team_for(db, owner, lock=True)
    if team is None:
        raise TeamError('no_team', 'Choose a team lead first.')
    agent = await member(db, owner, agent_id)
    if remove:
        if agent.team_id != team.id:
            raise TeamError('unreachable_agent', 'This agent is outside this team.')
        if team.lead_agent_id == agent.id:
            raise TeamError('lead_required', 'Choose another lead before removing this one.')
        agent.team_id = None
        audit(db, team, 'member_removed', agent_id=agent.id)
    else:
        if agent.team_id not in (None, team.id):
            raise TeamError('other_team', 'This agent belongs to another team.')
        if agent.team_id is None:
            agent.team_id = team.id
            audit(db, team, 'member_added', agent_id=agent.id)
        if delegation_enabled is not None:
            agent.delegation_enabled = delegation_enabled
    await db.commit()
    return agent


def clean_line(value, limit, label):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise TeamError('invalid_input', f'{label} must be one line, at most {limit} characters.')
    return agent_memory.redact_secrets(value.strip())[0]


async def create_specialist(db, owner, caller_id, turn_id, arguments):
    from app.core.config import settings
    await begin_write(db)
    caller = await member(db, owner, caller_id)
    team = await team_for(db, owner, lock=True)
    if not team or caller.team_id != team.id or team.lead_agent_id != caller.id:
        raise TeamError('lead_only', 'Only the team lead can add specialists.')
    unknown = set(arguments) - {'name', 'title', 'instructions', 'share_workspace'}
    if unknown:
        raise TeamError('invalid_input', 'Unrecognised specialist settings.')
    name = clean_line(arguments.get('name'), 80, 'Name')
    title = clean_line(arguments.get('title', 'Specialist'), 120, 'Title')
    instructions = arguments.get('instructions', '')
    if not isinstance(instructions, str) or len(instructions) > 2000:
        raise TeamError('invalid_input', 'Instructions must be at most 2000 characters.')
    instructions = agent_memory.redact_secrets(instructions)[0]
    count = await db.scalar(select(func.count()).select_from(TeamEvent).where(
        TeamEvent.team_id == team.id, TeamEvent.kind == 'agent_created', TeamEvent.data['turn_id'].as_integer() == turn_id))
    if count >= settings.TEAM_MAX_CREATED_PER_TURN:
        raise TeamError('creation_limit', 'This turn reached its specialist creation limit.')
    total = await db.scalar(select(func.count()).select_from(Agent).where(Agent.owner_id == owner, Agent.archived_at.is_(None)))
    if total >= settings.TEAM_MAX_AGENTS:
        raise TeamError('agent_limit', 'The live agent limit has been reached.')
    if await db.scalar(select(Agent.id).where(Agent.owner_id == owner, Agent.name == name, Agent.archived_at.is_(None))):
        raise TeamError('name_taken', 'A live agent already uses that name.')
    workspace = None
    if arguments.get('share_workspace'):
        if not caller.workspace_path:
            raise TeamError('project_required', 'Give the lead a project folder first.')
        workspace = caller.workspace_path
    agent = Agent(owner_id=owner, name=name, title=title, instructions=instructions,
                  team_id=team.id, created_by_agent_id=caller.id, provider=caller.provider,
                  model=caller.model, approval_mode='ask', workspace_path=workspace)
    db.add(agent)
    await db.flush()
    audit(db, team, 'agent_created', agent_id=agent.id, data={'turn_id': turn_id, 'instructions_length': len(instructions)})
    await db.commit()
    return agent


async def _used(db, agent):
    used = await db.scalar(select(Thread.id).where(Thread.agent_id == agent.id).limit(1))
    return bool(used or await db.scalar(select(AgentJob.id).where(AgentJob.assignee_agent_id == agent.id).limit(1)))


async def undo_available(db, owner, agent_id):
    """Undo of a lead-made specialist holds until the specialist is first used."""
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.owner_id != owner or agent.archived_at is not None or agent.created_by_agent_id is None:
        return False
    return not await _used(db, agent)


async def undo_create(db, owner, agent_id):
    await begin_write(db)
    agent = await member(db, owner, agent_id)
    if agent.created_by_agent_id is None:
        raise TeamError('not_created_by_lead', 'This agent was added by the person.')
    if await _used(db, agent):
        raise TeamError('agent_used', 'This specialist has already been used. Archive it from Agents instead.')
    team = await db.get(Team, agent.team_id)
    await db.delete(agent)
    if team:
        audit(db, team, 'agent_create_undone', agent_id=agent_id, data={'removed': 'deleted'})
    await db.commit()
    return 'deleted'
