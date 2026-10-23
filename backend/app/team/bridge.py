"""One turn's mailbox and atomic approval consumption."""
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from sqlalchemy import select, update
from app.db.models import Approval, Turn, Agent
from app.db.transactions import begin_write
from app.team import fence, notices, policy, service, setup


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


async def consume_allowed_approval(db, turn_id, owner_token, tool, arguments):
    await begin_write(db)
    turn = await db.get(Turn, turn_id)
    if turn is None or turn.status != 'running' or turn.owner_token != owner_token:
        await db.rollback()
        return False
    threshold = service.now() - timedelta(minutes=15)
    approvals = (await db.execute(select(Approval).where(
        Approval.turn_id == turn_id, Approval.tool == tool, Approval.status == 'allowed',
        Approval.consumed_at.is_(None), Approval.decided_at >= threshold).order_by(Approval.created_at))).scalars()
    expected = canonical(arguments)
    for approval in approvals:
        if canonical(approval.input) != expected:
            continue
        claimed = await db.execute(update(Approval).where(Approval.id == approval.id, Approval.consumed_at.is_(None), Approval.status == 'allowed').values(consumed_at=service.now()))
        await db.commit()
        return claimed.rowcount == 1
    await db.rollback()
    return False


class TeamBridge:
    def __init__(self, runtime, run):
        self.runtime, self.run = runtime, run
        # A fresh private folder outside all agent workspaces. Not backed up.
        self.folder = Path(tempfile.mkdtemp(prefix=f'open-dots-team-{run.turn_id}-'))
        self.task = None
        self.calls = set()

    @property
    def launch(self):
        extra = ['--escalations'] if notices.escalations_on() else []
        if getattr(sys, 'frozen', False):
            return SimpleNamespace(command=sys.executable, args=['--team-mcp', '--mailbox', str(self.folder), *extra], env={})
        root = str(Path(__file__).resolve().parents[2])
        return SimpleNamespace(command=sys.executable, args=['-m', 'app.team.mcp_server', '--mailbox', str(self.folder), *extra], env={'PYTHONPATH': root})

    def start(self):
        self.task = asyncio.create_task(self.poll())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for task in self.calls:
            task.cancel()
        await asyncio.gather(*self.calls, return_exceptions=True)
        shutil.rmtree(self.folder, ignore_errors=True)

    async def poll(self):
        while not self.run.ctx.aborted:
            for path in self.folder.glob('req-*.json'):
                claimed = path.with_suffix('.working')
                try:
                    path.rename(claimed)
                except FileNotFoundError:
                    continue
                task = asyncio.create_task(self.handle(claimed))
                self.calls.add(task)
                task.add_done_callback(self.calls.discard)
            await asyncio.sleep(.1)

    async def handle(self, path):
        try:
            if path.stat().st_size > 64000:
                raise service.TeamError('too_large', 'The team request is too large.')
            request = json.loads(path.read_text(encoding='utf-8'))
            name, args = request['name'], request['arguments']
            if not isinstance(name, str) or not isinstance(args, dict):
                raise ValueError('Invalid request')
            async with self.runtime.session() as db:
                allowed = await consume_allowed_approval(db, self.run.turn_id, self.run.owner_token, 'mcp__team__' + name, args)
            if not allowed:
                result = {'error': 'This call has no matching, unused approval.', 'code': 'not_approved'}
            else:
                result = await self.execute(name, args)
        except service.TeamError as exc:
            result = {'error': str(exc), 'code': exc.code}
        except Exception:
            result = {'error': 'The team call could not be completed.', 'code': 'team_error'}
        response = self.folder / path.name.replace('req-', 'resp-').replace('.working', '.json')
        tmp = response.with_suffix('.tmp')
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, response)
        path.unlink(missing_ok=True)

    async def execute(self, name, args):
        rt, run = self.runtime, self.run
        async with rt.session() as db:
            caller = await service.member(db, rt.owner_id, run.agent_id, team_id=run.ctx.team.team_id)
            if name == 'list_agents':
                rows = (await db.execute(select(Agent).where(Agent.team_id == caller.team_id, Agent.owner_id == rt.owner_id, Agent.archived_at.is_(None)).order_by(Agent.id))).scalars()
                return {'agents': [{'id': a.id, 'name': fence.line(a.name), 'title': fence.line(a.title), 'can_delegate': a.delegation_enabled} for a in rows]}
            if name == 'create_agent':
                # A permission card cannot widen the immutable origin or plan floor.
                if run.ctx.approval_mode == 'plan' or run.ctx.team.origin in policy.UNATTENDED:
                    raise service.TeamError('forbidden', 'Creating an agent is unavailable for this turn.')
                agent = await service.create_specialist(db, rt.owner_id, run.agent_id, run.turn_id, args)
                await run.recorder.emit('team.agent.created', {'agent_id': agent.id, 'name': agent.name, 'title': agent.title, 'created_by_agent_id': run.agent_id, 'undo_until_used': True})
                return {'agent_id': agent.id, 'name': agent.name, 'approval_mode': 'ask'}
        if name in setup.TOOLS:
            return await setup.call(rt, run, name, args)
        if name == 'ask_agent':
            return await rt.team_jobs.ask(run, args)
        if name == 'delegate':
            return await rt.team_jobs.delegate(run, args)
        if name == 'job_status':
            return await rt.team_jobs.job_status(run, args)
        if name == 'retry_thread':
            return await rt.notices.retry_thread(run, args)
        if name == 'raise_escalation':
            return await rt.notices.raise_escalation(run, args)
        if name in ('list_rooms', 'create_room', 'say_in_room'):
            return await rt.rooms.tool(run, name, args)
        raise service.TeamError('unknown_tool', 'Unknown team tool.')
