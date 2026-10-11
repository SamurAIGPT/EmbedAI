import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models.task import Tasks
from app.db.models.user_credential import UserCredential
from app.db.session import get_db
from app.routers.task import router
from app.services import user_credential_service
from tests.routers.test_reply_active_conversation import conversation_boat_api  # noqa: F401


async def save_credential(db, user):
    await user_credential_service.save_token(
        db, user_id=user, provider="claude", file_path=".claude/.credentials.json",
        plaintext=json.dumps({"claudeAiOauth": {"expiresAt": 1}}),
    )


def task_app(session_dependency):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = session_dependency
    return app


async def submit(client, user, parent=None):
    return await client.post("/api/v1/tasks", json={
        "user_id": user, "provider": "claude", "box_id": "bx_abcdefgh",
        "prompt_text": "continue report", "parent_task_id": parent,
    })


async def complete(client, state, response):
    payload = response.json()
    state["completed"].add(int(payload["prompt_id"]))
    completed = await client.get(f"/api/v1/tasks/{payload['id']}")
    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    return payload


@pytest.mark.parametrize("status", ["queued", "starting", "pending", "running", "waiting_approval"])
@pytest.mark.parametrize("target", ["ancestor", "direct-parent"])
async def test_reply_refuses_every_active_conversation_status(
    db_session, user_id, monkeypatch, conversation_boat_api, status, target
):
    base_url, state = conversation_boat_api
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    await save_credential(db_session, user_id)
    app = task_app(lambda: db_session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:
        initial = await submit(client, user_id)
        assert initial.status_code == 200
        ancestor = await complete(client, state, initial)
        reply = await submit(client, user_id, ancestor["id"])
        assert reply.status_code == 200
        active = reply.json()
        # Cover persisted pre-launch and permission-wait states as well as
        # the running status produced by this actual API/SDK launch.
        row = await db_session.get(Tasks, active["id"])
        row.status = status
        await db_session.commit()
        count = len(state["commands"])
        refused = await submit(client, user_id, ancestor["id"] if target == "ancestor" else active["id"])
    assert refused.status_code == 409
    assert status in refused.json()["detail"]
    assert len(state["commands"]) == count
    rows = (await db_session.execute(select(Tasks).where(Tasks.user_id == user_id))).scalars().all()
    assert len(rows) == 2


async def test_reply_wrong_user_remains_not_found(
    db_session, user_id, other_user_id, monkeypatch, conversation_boat_api
):
    base_url, state = conversation_boat_api
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    await save_credential(db_session, user_id)
    app = task_app(lambda: db_session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:
        initial = await submit(client, user_id)
        assert initial.status_code == 200
        ancestor = await complete(client, state, initial)
        refused = await submit(client, other_user_id, ancestor["id"])
    assert refused.status_code == 404
    assert len(state["commands"]) == 1


async def wait_for_database_lock_waiters(sessions, application, count, requests):
    """A server-observed barrier: real request transactions wait on row locks."""
    async with asyncio.timeout(10):
        while True:
            async with sessions() as monitor:
                waiting = (await monitor.execute(text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name = :application "
                    "AND wait_event_type = 'Lock' AND query LIKE '%FOR UPDATE%'"
                ), {"application": application})).scalar_one()
            if waiting == count:
                return waiting
            if all(request.done() for request in requests):
                raise AssertionError(f"Requests finished before PostgreSQL lock barrier: {waiting}/{count}")
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("independent", [False, True], ids=["distinct-finished-ancestors", "independent-conversation"])
async def test_reply_admission_is_atomic_across_transactions(
    monkeypatch, conversation_boat_api, independent
):
    base_url, state = conversation_boat_api
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    user = "web_reply_lock_" + uuid4().hex
    application = "reply_lock_" + uuid4().hex
    engine = create_async_engine(settings.ASYNC_DATABASE_URL, connect_args={
        "server_settings": {"application_name": application},
    })
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def request_session():
        async with sessions() as db:
            yield db

    app = task_app(request_session)
    requests = []
    try:
        async with sessions() as db:
            await save_credential(db, user)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:
            initial = await submit(client, user)
            assert initial.status_code == 200
            first = await complete(client, state, initial)
            next_task = await submit(client, user, None if independent else first["id"])
            assert next_task.status_code == 200
            second = await complete(client, state, next_task)
            assert (first["session_id"] != second["session_id"]) is independent
            launches_before = len(state["commands"])
            async with sessions() as holder:
                # Hold the oldest conversation row before both API requests.
                # Each API request owns a separate committed PG transaction.
                await holder.execute(select(Tasks.id).where(Tasks.id == first["id"]).with_for_update())
                requests.append(asyncio.create_task(submit(client, user, first["id"])))
                requests.append(asyncio.create_task(submit(client, user, second["id"])))
                try:
                    if independent:
                        admitted = await asyncio.wait_for(asyncio.shield(requests[1]), timeout=5)
                        assert admitted.status_code == 200
                        assert not requests[0].done()
                        assert await wait_for_database_lock_waiters(sessions, application, 1, requests) == 1
                    else:
                        assert await wait_for_database_lock_waiters(sessions, application, 2, requests) == 2
                finally:
                    await holder.rollback()
            results = await asyncio.wait_for(asyncio.gather(*requests), timeout=10)
            assert sorted(response.status_code for response in results) == ([200, 200] if independent else [200, 409])
            assert len(state["commands"]) - launches_before == (2 if independent else 1)
            for response in results:
                if response.status_code == 200:
                    assert state["commands"][-1].split()[-2] == "--resume"
                    assert response.json()["session_id"] in {first["session_id"], second["session_id"]}
            async with sessions() as db:
                rows = (await db.execute(select(Tasks).where(Tasks.user_id == user))).scalars().all()
                assert len(rows) == (4 if independent else 3)
    finally:
        for request in requests:
            if not request.done():
                request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        async with sessions() as db:
            await db.execute(delete(Tasks).where(Tasks.user_id == user))
            await db.execute(delete(UserCredential).where(UserCredential.user_id == user))
            await db.commit()
        await engine.dispose()
