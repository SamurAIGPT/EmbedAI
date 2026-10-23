"""The /rooms HTTP routes and the undo window for lead-made specialists."""
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from app.db.models import RoomDispatch
from app.routers.rooms import router as rooms_router
from app.routers.team import router as team_router, get_runtime
from app.services.local_access_policy import require_local_access
from app.team import service
from tests.services.thread_harness import sessions, owner, cleanup, wait_until  # noqa: F401
from tests.team.test_collaboration import Engine, harness  # noqa: F401


@pytest.fixture
async def api(harness):
    rt = harness[0]
    app = FastAPI()
    app.include_router(rooms_router)
    app.include_router(team_router)
    app.dependency_overrides[get_runtime] = lambda: rt
    app.dependency_overrides[require_local_access] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client


async def make_room(api, harness, **extra):
    rt, engine, lead, worker = harness
    body = {'name': 'Desk', 'member_ids': [lead.id, worker.id], 'default_responder_kind': 'mentions', **extra}
    response = await api.post('/rooms', json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_list_get_update_delete(api, harness):
    room = await make_room(api, harness)
    assert [r['id'] for r in (await api.get('/rooms')).json()] == [room['id']]
    assert (await api.get(f"/rooms/{room['id']}")).json()['name'] == 'Desk'
    updated = await api.patch(f"/rooms/{room['id']}", json={'name': 'Renamed', 'bulletin': 'Ship it'})
    assert updated.status_code == 200 and updated.json()['name'] == 'Renamed'
    assert (await api.delete(f"/rooms/{room['id']}")).status_code == 204
    assert (await api.get(f"/rooms/{room['id']}")).status_code == 404


async def test_validation_and_missing(api, harness):
    assert (await api.post('/rooms', json={'name': '', 'member_ids': [1]})).status_code == 422
    assert (await api.post('/rooms', json={'name': 'x', 'member_ids': [], 'extra': 1})).status_code == 422
    assert (await api.get('/rooms/9999')).status_code == 404
    assert (await api.post('/rooms/9999/messages', json={'text': 'hi'})).status_code == 404


async def test_post_messages_read_and_export(api, harness):
    rt, engine, lead, worker = harness
    room = await make_room(api, harness)
    posted = await api.post(f"/rooms/{room['id']}/messages", json={'text': 'hello team'})
    assert posted.status_code == 201 and posted.json()['seq'] >= 1
    rows = (await api.get(f"/rooms/{room['id']}/messages")).json()
    assert any(r.get('text') == 'hello team' for r in rows)
    read = await api.post(f"/rooms/{room['id']}/read", json={'seq': 10_000})
    assert read.status_code == 200 and read.json()['seq'] <= posted.json()['seq'] + 5
    md = await api.get(f"/rooms/{room['id']}/export")
    assert md.status_code == 200 and 'hello team' in md.text and 'room-' in md.headers['content-disposition']
    js = await api.get(f"/rooms/{room['id']}/export", params={'format': 'json'})
    assert js.json()['room'] == 'Desk'


async def test_hold_stop_and_retry_rules(api, harness, sessions):
    rt, engine, lead, worker = harness
    room = await make_room(api, harness, default_responder_kind='everyone')
    held = await api.patch(f"/rooms/{room['id']}/members/{worker.id}", json={'held': True})
    assert held.status_code == 200
    assert (await api.post(f"/rooms/{room['id']}/stop", json={})).status_code == 202
    assert (await api.get(f"/rooms/{room['id']}/approvals")).json() == []
    # Only failed or stopped replies may be retried.
    await api.patch(f"/rooms/{room['id']}/members/{worker.id}", json={'held': False})
    await api.post(f"/rooms/{room['id']}/messages", json={'text': 'work please'})
    async with sessions() as db:
        rows = list((await db.execute(select(RoomDispatch).where(RoomDispatch.room_id == room['id']))).scalars())
    assert rows
    async with sessions() as db:
        row = await db.get(RoomDispatch, rows[0].id)
        row.status = 'succeeded'
        await db.commit()
    refused = await api.post(f"/rooms/{room['id']}/dispatches/{rows[0].id}/retry")
    assert refused.status_code == 409
    async with sessions() as db:
        row = await db.get(RoomDispatch, rows[0].id)
        row.status = 'stopped'
        await db.commit()
    ok = await api.post(f"/rooms/{room['id']}/dispatches/{rows[0].id}/retry")
    assert ok.status_code == 202 and ok.json() == {'queued': True}


async def test_undo_holds_until_the_specialist_is_used(api, harness, sessions, owner):
    rt, engine, lead, worker = harness
    async with sessions() as db:
        fresh = await service.create_specialist(db, owner, lead.id, 900, {'name': 'Fresh', 'instructions': 'Do useful work'})
        used = await service.create_specialist(db, owner, lead.id, 900, {'name': 'Used', 'instructions': 'Do useful work'})
    await rt.create_thread(agent_id=used.id)
    assert (await api.get(f'/team/agents/{fresh.id}/undo-state')).json() == {'available': True}
    assert (await api.get(f'/team/agents/{used.id}/undo-state')).json() == {'available': False}
    refused = await api.post(f'/team/agents/{used.id}/undo-create')
    assert refused.status_code == 409 and refused.json()['detail']['error_code'] == 'agent_used'
    assert (await api.post(f'/team/agents/{fresh.id}/undo-create')).json() == {'removed': 'deleted'}
    assert (await api.get(f'/team/agents/{fresh.id}/undo-state')).json() == {'available': False}
