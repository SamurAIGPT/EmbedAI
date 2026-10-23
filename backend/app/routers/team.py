"""Manage a team and inspect its durable jobs."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from app.db.models import Agent, Team, TeamEvent, AgentJob, AgentJobEvent, Approval, Thread, Turn
from app.db.transactions import begin_write
from app.services.local_access_policy import require_local_access
from app.routers.threads import get_runtime
from app.team import service, jobs
from app.services.engine_registry import capabilities_of
from app.core.config import settings

router = APIRouter(prefix='/team', tags=['Team'], dependencies=[Depends(require_local_access)])


class LeadRequest(BaseModel):
    agent_id: int
    name: str = Field(default='My team', min_length=1, max_length=80)


class MemberRequest(BaseModel):
    agent_id: int


class MemberUpdate(BaseModel):
    delegation_enabled: bool


class TeamUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def error(exc):
    return HTTPException(status_code=404 if exc.code.endswith('not_found') else 409,
                         detail={'error_code': exc.code, 'message': str(exc)})


@router.get('')
async def get_team(rt=Depends(get_runtime)):
    async with rt.session() as db:
        team = await service.team_for(db, rt.owner_id)
        if not team:
            return {'team': None, 'agents': [], 'limits': {'max_agents': settings.TEAM_MAX_AGENTS, 'max_depth': settings.TEAM_MAX_DEPTH}, 'lead_ready': {'ok': False, 'reason': 'Choose a team lead.'}}
        rows = list((await db.execute(select(Agent).where(Agent.owner_id == rt.owner_id, Agent.team_id == team.id, Agent.archived_at.is_(None)).order_by(Agent.id))).scalars())
        output = []
        for agent in rows:
            from app.schemas.agent import AgentResponse
            data = AgentResponse.model_validate(agent).model_dump()
            running = await db.scalar(select(Turn.id).join(Thread, Thread.id == Turn.thread_id).where(Thread.agent_id == agent.id, Turn.status == 'running').limit(1))
            waiting = await db.scalar(select(Approval.id).join(Thread, Thread.id == Approval.thread_id).where(Thread.agent_id == agent.id, Approval.status == 'pending').limit(1))
            data.update(is_lead=team.lead_agent_id == agent.id, state='waiting_on_you' if waiting else 'working' if running else 'idle', instructions_reviewed=agent.created_by_agent_id is None or agent.instructions_reviewed_at is not None)
            output.append(data)
        lead = next((a for a in rows if a.id == team.lead_agent_id), None)
        available = bool(lead and lead_engine_ok(rt, lead))
        return {'team': {'id': team.id, 'name': team.name, 'lead_agent_id': team.lead_agent_id}, 'agents': output,
                'limits': {'max_agents': settings.TEAM_MAX_AGENTS, 'max_depth': settings.TEAM_MAX_DEPTH, 'max_parallel': settings.TEAM_MAX_PARALLEL},
                'lead_ready': {'ok': available, 'reason': None if available else 'This lead needs an engine with team tools (Claude).'}}


def lead_engine_ok(rt, agent):
    adapter = rt.adapters.get(agent.provider) if agent else None
    return bool(adapter and capabilities_of(adapter).mcp)


@router.post('/lead')
async def lead(payload: LeadRequest, rt=Depends(get_runtime)):
    try:
        async with rt.session() as db:
            candidate = await db.get(Agent, payload.agent_id)
            if candidate is not None and not lead_engine_ok(rt, candidate):
                raise service.TeamError('lead_engine', 'A lead needs an engine with team tools (Claude).')
            await service.set_lead(db, rt.owner_id, payload.agent_id, payload.name)
    except service.TeamError as exc:
        raise error(exc) from None
    return await get_team(rt)


@router.patch('')
async def update_team(payload: TeamUpdate, rt=Depends(get_runtime)):
    async with rt.session() as db:
        await begin_write(db)
        team = await service.team_for(db, rt.owner_id, lock=True)
        if not team:
            raise HTTPException(404, 'Team not found')
        team.name = service.clean_line(payload.name, 80, 'Name')
        await db.commit()
    return await get_team(rt)


@router.post('/members')
async def add_member(payload: MemberRequest, rt=Depends(get_runtime)):
    try:
        async with rt.session() as db:
            await service.change_member(db, rt.owner_id, payload.agent_id)
    except service.TeamError as exc:
        raise error(exc) from None
    return await get_team(rt)


@router.patch('/members/{agent_id}')
async def patch_member(agent_id: int, payload: MemberUpdate, rt=Depends(get_runtime)):
    try:
        async with rt.session() as db:
            await service.change_member(db, rt.owner_id, agent_id, delegation_enabled=payload.delegation_enabled)
    except service.TeamError as exc:
        raise error(exc) from None
    return await get_team(rt)


@router.delete('/members/{agent_id}')
async def remove_member(agent_id: int, rt=Depends(get_runtime)):
    try:
        async with rt.session() as db:
            await service.change_member(db, rt.owner_id, agent_id, remove=True)
    except service.TeamError as exc:
        raise error(exc) from None
    return await get_team(rt)


@router.post('/agents/{agent_id}/undo-create')
async def undo_create(agent_id: int, rt=Depends(get_runtime)):
    try:
        async with rt.session() as db:
            return {'removed': await service.undo_create(db, rt.owner_id, agent_id)}
    except service.TeamError as exc:
        raise error(exc) from None


async def owned_job(rt, db, job_id):
    job = await db.get(AgentJob, job_id)
    if job is None or job.owner_id != rt.owner_id:
        raise HTTPException(404, 'Job not found')
    return job


@router.get('/jobs')
async def list_jobs(root_id: int | None = None, thread_id: int | None = None, status: str | None = None,
                    limit: int = Query(100, ge=1, le=500), rt=Depends(get_runtime)):
    async with rt.session() as db:
        query = select(AgentJob).where(AgentJob.owner_id == rt.owner_id)
        if root_id is not None:
            query = query.where((AgentJob.root_id == root_id) | (AgentJob.id == root_id))
        if thread_id is not None:
            query = query.where(AgentJob.requester_thread_id == thread_id)
        if status:
            query = query.where(AgentJob.status == status)
        rows = list((await db.execute(query.order_by(AgentJob.id.desc()).limit(limit))).scalars())
        return [{**jobs.public(row), 'brief': row.brief[:400], 'result': None} for row in rows]


@router.get('/jobs/{job_id}')
async def job_detail(job_id: int, rt=Depends(get_runtime)):
    async with rt.session() as db:
        job = await owned_job(rt, db, job_id)
        children = list((await db.execute(select(AgentJob).where(AgentJob.parent_id == job_id).order_by(AgentJob.id))).scalars())
        events = list((await db.execute(select(AgentJobEvent).where(AgentJobEvent.job_id == job_id).order_by(AgentJobEvent.id))).scalars())
        approvals = list((await db.execute(select(Approval).where(Approval.turn_id == job.assignee_turn_id, Approval.status == 'pending'))).scalars()) if job.assignee_turn_id else []
        from app.schemas.thread import ApprovalResponse
        usage = (await db.get(Turn, job.assignee_turn_id)).usage if job.assignee_turn_id else None
        return {**jobs.public(job), 'children': [jobs.public(c) for c in children],
                'events': [{'kind': e.kind, 'at': e.at, 'detail': e.detail} for e in events],
                'approvals': [ApprovalResponse.model_validate(a) for a in approvals], 'usage': usage}


@router.post('/jobs/{job_id}/cancel', status_code=202)
async def cancel_job(job_id: int, rt=Depends(get_runtime)):
    async with rt.session() as db:
        await owned_job(rt, db, job_id)
    await rt.team_jobs.cancel(job_id)
    return {'cancelled': True}


@router.get('/activity')
async def activity(limit: int = Query(100, ge=1, le=500), rt=Depends(get_runtime)):
    async with rt.session() as db:
        rows = list((await db.execute(select(TeamEvent).where(TeamEvent.owner_id == rt.owner_id).order_by(TeamEvent.id.desc()).limit(limit))).scalars())
        return [{'id': e.id, 'kind': e.kind, 'agent_id': e.agent_id, 'job_id': e.job_id, 'thread_id': e.thread_id, 'data': e.data, 'created_at': e.created_at} for e in rows]
