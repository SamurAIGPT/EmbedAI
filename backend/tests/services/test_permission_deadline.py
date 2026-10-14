import asyncio
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.db.models.permission_ask import PermissionAsk
from app.db.engine import create_app_async_engine
from app.db.models.task import Tasks
from app.db.session import get_db
from app.main import app
from app.routers import permission as permission_router


@pytest.mark.parametrize("answer,phase", [("allow", "last"), ("deny", "last"), ("allow", "early"), (None, "never")])
async def test_hook_honors_answer_in_final_poll_interval(monkeypatch, answer, phase):
    engine = create_app_async_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = "web_deadline_" + uuid4().hex
    first_read, second_read = asyncio.Event(), asyncio.Event()
    reads = 0

    def observed_read(connection, cursor, statement, params, context, executemany):
        nonlocal reads
        if context.execution_options.get("populate_existing") and "FROM permission_ask" in statement:
            reads += 1
            if reads == 1:
                first_read.set()
            elif reads == 2:
                second_read.set()

    async def request_session():
        async with sessions() as db:
            yield db

    event.listen(engine.sync_engine, "after_cursor_execute", observed_read)
    monkeypatch.setitem(app.dependency_overrides, get_db, request_session)
    # Keep the actual two-poll request/answer flow; reduce only its configured wall-clock budget.
    monkeypatch.setattr(permission_router, "ASK_POLL_TIMEOUT_SECONDS", 0.4)
    monkeypatch.setattr(permission_router, "ASK_POLL_INTERVAL_SECONDS", 0.2)
    hook = None
    try:
        async with sessions() as db:
            task = Tasks(user_id=user, provider="claude", box_id="owned", prompt_text="write report", status="running")
            db.add(task)
            await db.commit()
            task_id = task.id
        async with AsyncClient(transport=ASGITransport(app), base_url="http://owned.test") as client:
            hook = asyncio.create_task(client.post(
                f"/api/v1/permissions/check/{user}/{task_id}",
                headers={"Authorization": f"Bearer {settings.HOOK_TOKEN}"},
                json={"tool_name": "Write", "tool_input": {}},
            ))
            if answer is not None:
                await asyncio.wait_for(second_read.wait() if phase == "last" else first_read.wait(), timeout=3)
                # The native SELECT has completed. Let its caller inspect pending before the real web answer.
                await asyncio.sleep(0.04)
                pending = await client.get(f"/api/v1/permissions/ask/task/{task_id}")
                assert pending.status_code == 200 and pending.json()["status"] == "pending"
                answered = await client.post(f"/api/v1/permissions/ask/{pending.json()['id']}/answer", json={"decision": answer})
                assert answered.status_code == 200 and answered.json()["decision"] == answer
            result = await asyncio.wait_for(hook, timeout=3)
            assert result.status_code == 200
            assert result.json()["hookSpecificOutput"]["decision"]["behavior"] == (answer or "deny")
        async with sessions() as db:
            ask = (await db.execute(select(PermissionAsk).where(PermissionAsk.task_id == task_id))).scalar_one()
            assert ask.status == ("answered" if answer else "expired")
            assert (await db.get(Tasks, task_id)).status == "running"
    finally:
        if hook is not None and not hook.done():
            hook.cancel()
            await asyncio.gather(hook, return_exceptions=True)
        async with sessions() as db:
            await db.execute(delete(PermissionAsk).where(PermissionAsk.user_id == user))
            await db.execute(delete(Tasks).where(Tasks.user_id == user))
            await db.commit()
        event.remove(engine.sync_engine, "after_cursor_execute", observed_read)
        await engine.dispose()
