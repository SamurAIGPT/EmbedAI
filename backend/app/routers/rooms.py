"""Shared conversations and member controls."""
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select
from app.db.models import Approval, RoomMember, RoomDispatch, Thread
from app.db.transactions import begin_write
from app.routers.threads import get_runtime
from app.services.local_access_policy import require_local_access
from app.services.agent_memory import redact_secrets
from app.team.service import TeamError, now
from app.routers.team import error
from app.schemas.thread import ApprovalResponse

router = APIRouter(prefix='/rooms', tags=['Rooms'], dependencies=[Depends(require_local_access)])


class CreateRoom(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    member_ids: list[int] = Field(min_length=1, max_length=12)
    bulletin: str = Field(default='', max_length=12000)
    default_responder_kind: Literal['member', 'everyone', 'mentions'] = 'member'
    default_responder_agent_id: int | None = None


class UpdateRoom(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=80)
    bulletin: str | None = Field(default=None, max_length=12000)
    default_responder_kind: Literal['member', 'everyone', 'mentions'] | None = None
    default_responder_agent_id: int | None = None


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class Read(BaseModel):
    seq: int = Field(ge=0)


class Hold(BaseModel):
    held: bool


class Stop(BaseModel):
    agent_id: int | None = None


@router.get('')
async def list_rooms(rt=Depends(get_runtime)):
    return await rt.rooms.list()


@router.post('', status_code=201)
async def create_room(payload: CreateRoom, rt=Depends(get_runtime)):
    try:
        return await rt.rooms.create(payload.model_dump(exclude_none=True))
    except TeamError as exc:
        raise error(exc) from None


@router.get('/{room_id}')
async def get_room(room_id: int, rt=Depends(get_runtime)):
    try:
        return await rt.rooms.detail(room_id)
    except TeamError as exc:
        raise error(exc) from None


@router.patch('/{room_id}')
async def update_room(room_id: int, payload: UpdateRoom, rt=Depends(get_runtime)):
    try:
        return await rt.rooms.update(room_id, payload.model_dump(exclude_unset=True))
    except TeamError as exc:
        raise error(exc) from None


@router.delete('/{room_id}', status_code=204)
async def delete_room(room_id: int, rt=Depends(get_runtime)):
    try:
        await rt.rooms.delete(room_id)
    except TeamError as exc:
        raise error(exc) from None


@router.get('/{room_id}/messages')
async def messages(room_id: int, rt=Depends(get_runtime)):
    try:
        return await rt.rooms.messages(room_id)
    except TeamError as exc:
        raise error(exc) from None


@router.post('/{room_id}/messages', status_code=201)
async def post(room_id: int, payload: Message, request: Request, rt=Depends(get_runtime)):
    try:
        from app.auth.middleware import principal_of
        principal = principal_of(request.scope)
        seq = await rt.rooms.post(room_id, payload.text, source={'origin': 'person' if principal is None or principal.is_admin else 'restricted'})
        # A saved post is independent of dispatch startup. Scheduler tick also
        # picks it up after a process restart.
        await rt.rooms.tick()
        return {'seq': seq}
    except TeamError as exc:
        raise error(exc) from None


@router.post('/{room_id}/read')
async def read(room_id: int, payload: Read, rt=Depends(get_runtime)):
    try:
        async with rt.session() as db:
            await begin_write(db)
            room = await rt.rooms.get(db, room_id, lock=True)
            thread = await db.get(Thread, room.thread_id)
            room.last_read_seq = max(room.last_read_seq, min(payload.seq, thread.last_seq))
            await db.commit()
        return {'seq': room.last_read_seq}
    except TeamError as exc:
        raise error(exc) from None


@router.patch('/{room_id}/members/{agent_id}')
async def hold(room_id: int, agent_id: int, payload: Hold, rt=Depends(get_runtime)):
    try:
        return await rt.rooms.hold(room_id, agent_id, payload.held)
    except TeamError as exc:
        raise error(exc) from None


@router.post('/{room_id}/stop', status_code=202)
async def stop(room_id: int, payload: Stop | None = None, rt=Depends(get_runtime)):
    try:
        return {'stopped': await rt.rooms.stop(room_id, payload.agent_id if payload else None)}
    except TeamError as exc:
        raise error(exc) from None


@router.get('/{room_id}/approvals', response_model=list[ApprovalResponse])
async def approvals(room_id: int, rt=Depends(get_runtime)):
    try:
        async with rt.session() as db:
            await rt.rooms.get(db, room_id)
            return list((await db.execute(select(Approval).join(RoomMember, RoomMember.member_thread_id == Approval.thread_id).where(RoomMember.room_id == room_id, Approval.status == 'pending').order_by(Approval.created_at))).scalars())
    except TeamError as exc:
        raise error(exc) from None


@router.post('/{room_id}/dispatches/{dispatch_id}/retry', status_code=202)
async def retry(room_id: int, dispatch_id: int, rt=Depends(get_runtime)):
    try:
        async with rt.session() as db:
            await begin_write(db)
            await rt.rooms.get(db, room_id, lock=True)
            row = await db.get(RoomDispatch, dispatch_id)
            if row is None or row.room_id != room_id or row.status not in ('failed', 'stopped'):
                raise HTTPException(409, 'Only failed or stopped replies can be retried.')
            # Explicit person action resets the existing unique delivery row;
            # it never creates duplicate delivery identities.
            row.status, row.reason, row.turn_id, row.error_code = 'queued', 'retry', None, None
            row.finished_at, row.started_at = None, None
            await db.commit()
        await rt.rooms.tick()
        return {'queued': True}
    except TeamError as exc:
        raise error(exc) from None


@router.get('/{room_id}/export')
async def export(room_id: int, format: Literal['md', 'json'] = 'md', rt=Depends(get_runtime)):
    try:
        room = await rt.rooms.detail(room_id)
        rows = await rt.rooms.messages(room_id)
    except TeamError as exc:
        raise error(exc) from None
    # Export contains visible messages only, and redacts text again.
    for row in rows:
        if 'text' in row:
            row['text'] = redact_secrets(row['text'])[0]
    if format == 'json':
        body = json.dumps({'room': room['name'], 'messages': rows}, ensure_ascii=False, indent=2, default=str)
        media = 'application/json'
    else:
        lines = ['# ' + room['name'], '']
        for row in rows:
            author = row.get('author', {}).get('label', 'Room')
            source = row.get('source') or {}
            label = f" ({source['kind']})" if source.get('kind') in ('routine', 'webhook') else ''
            lines.extend([f"## {author}{label} · {row['created_at']}", '', row.get('text', ''), ''])
        body, media = '\n'.join(lines), 'text/markdown'
    return Response(body, media_type=media, headers={'Content-Disposition': f'attachment; filename="room-{room_id}.{format}"'})
