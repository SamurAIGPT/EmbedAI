"""Reply admission under concurrent requests, the SQLite counterpart of
test_reply_conversation_lock.py (which proves the PostgreSQL row lock and is
skipped on SQLite, where there are no row locks).

Concurrent replies to one conversation must admit exactly one (the others get a
clean 409), concurrent replies to different conversations must all be admitted,
and no request may fail with a database error."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.db.engine import create_app_async_engine
from app.db.models.task import Tasks
from app.db.models.user_credential import UserCredential
from tests.routers.test_reply_active_conversation import conversation_boat_api  # noqa: F401
from tests.routers.test_reply_conversation_lock import complete, save_credential, submit, task_app

pytestmark = pytest.mark.skipif(
    settings.DATABASE_KIND != "sqlite",
    reason="PostgreSQL is covered by test_reply_conversation_lock.py (row locks)",
)

CONCURRENT_REPLIES = 4


async def test_concurrent_replies_to_one_conversation_admit_exactly_one(
    monkeypatch, conversation_boat_api
):
    base_url, state = conversation_boat_api
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    user = "web_reply_admission_" + uuid4().hex
    engine = create_app_async_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def request_session():
        async with sessions() as db:
            yield db

    app = task_app(request_session)
    try:
        async with sessions() as db:
            await save_credential(db, user)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:
            initial = await submit(client, user)
            assert initial.status_code == 200
            first = await complete(client, state, initial)
            second = await complete(client, state, await submit(client, user, first["id"]))
            ancestors = [first["id"], second["id"]] * (CONCURRENT_REPLIES // 2)
            launches_before = len(state["commands"])

            results = await asyncio.gather(*(submit(client, user, parent) for parent in ancestors))

            codes = sorted(response.status_code for response in results)
            assert codes == [200] + [409] * (CONCURRENT_REPLIES - 1), [r.text for r in results]
            assert len(state["commands"]) - launches_before == 1  # one reply was launched
            async with sessions() as db:
                rows = (await db.execute(select(Tasks).where(Tasks.user_id == user))).scalars().all()
            assert len(rows) == 3  # the two finished tasks and the one admitted reply
    finally:
        async with sessions() as db:
            await db.execute(delete(Tasks).where(Tasks.user_id == user))
            await db.execute(delete(UserCredential).where(UserCredential.user_id == user))
            await db.commit()
        await engine.dispose()


async def test_concurrent_replies_to_different_conversations_are_all_admitted(
    monkeypatch, conversation_boat_api
):
    base_url, state = conversation_boat_api
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    user = "web_reply_independent_" + uuid4().hex
    engine = create_app_async_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def request_session():
        async with sessions() as db:
            yield db

    app = task_app(request_session)
    try:
        async with sessions() as db:
            await save_credential(db, user)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:
            conversations = []
            for _ in range(CONCURRENT_REPLIES):
                conversations.append(await complete(client, state, await submit(client, user)))
            assert len({c["session_id"] for c in conversations}) == CONCURRENT_REPLIES

            results = await asyncio.gather(*(submit(client, user, c["id"]) for c in conversations))

            assert [response.status_code for response in results] == [200] * CONCURRENT_REPLIES, [
                r.text for r in results
            ]
            async with sessions() as db:
                rows = (await db.execute(select(Tasks).where(Tasks.user_id == user))).scalars().all()
            assert len(rows) == CONCURRENT_REPLIES * 2
    finally:
        async with sessions() as db:
            await db.execute(delete(Tasks).where(Tasks.user_id == user))
            await db.execute(delete(UserCredential).where(UserCredential.user_id == user))
            await db.commit()
        await engine.dispose()
