import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.task import Tasks
from app.db.session import get_db
from app.routers.local import router as local_router
from app.routers.task import router as task_router
from app.services import local_claude_runner, schedule_service, task_execution_service
from app.services.local_access_policy import LocalAccessGrant, _issue_grant
from app.services.schedule_service import InvalidScheduleError


def local_app(db_session) -> FastAPI:
    app = FastAPI()
    app.include_router(local_router, prefix="/api/v1")
    app.include_router(task_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    return app


def client_for(app, *, client=("127.0.0.1", 50000), base_url="http://localhost"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app, client=client), base_url=base_url
    )


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture
def ready_cli(monkeypatch):
    async def status():
        return local_claude_runner.CliStatus(installed=True, version="9.9.9", signed_in=True)

    monkeypatch.setattr(local_claude_runner, "get_status", status)


@pytest.fixture
def no_background_run(monkeypatch):
    started = []

    async def noop(task_id, snapshot=None):
        started.append(task_id)

    monkeypatch.setattr(task_execution_service, "_run_local_task", noop)
    return started


async def task_count(db_session) -> int:
    return (await db_session.execute(select(func.count()).select_from(Tasks))).scalar_one()


async def test_disabled_flag_refuses_everything(db_session, user_id, monkeypatch, ready_cli):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", False)
    async with client_for(local_app(db_session)) as client:
        created = await client.post(
            "/api/v1/local/tasks", json={"user_id": user_id, "prompt_text": "hi"}
        )
        status = await client.get("/api/v1/local/status")
    assert created.status_code == 403
    assert status.json() == {
        "enabled": False, "available": False, "reason": None, "cli_version": None,
        "tools_enabled": False,
    }


async def test_remote_client_is_refused(db_session, user_id, ready_cli, no_background_run):
    before = await task_count(db_session)
    async with client_for(local_app(db_session), client=("203.0.113.9", 4000)) as client:
        created = await client.post(
            "/api/v1/local/tasks", json={"user_id": user_id, "prompt_text": "hi"}
        )
        status = await client.get("/api/v1/local/status")
    assert created.status_code == 403
    assert status.json()["available"] is False
    assert "this machine" in status.json()["reason"]
    assert await task_count(db_session) == before
    assert no_background_run == []


async def test_tunnel_host_header_is_refused(db_session, user_id, ready_cli, no_background_run):
    async with client_for(local_app(db_session), base_url="https://abc.ngrok.app") as client:
        created = await client.post(
            "/api/v1/local/tasks", json={"user_id": user_id, "prompt_text": "hi"}
        )
    assert created.status_code == 403


async def test_foreign_origin_is_refused(db_session, user_id, ready_cli, no_background_run):
    async with client_for(local_app(db_session)) as client:
        created = await client.post(
            "/api/v1/local/tasks",
            json={"user_id": user_id, "prompt_text": "hi"},
            headers={"Origin": "https://evil.example"},
        )
    assert created.status_code == 403


async def test_status_reports_ready_cli(db_session, ready_cli):
    async with client_for(local_app(db_session)) as client:
        status = await client.get("/api/v1/local/status")
    assert status.json() == {
        "enabled": True, "available": True, "reason": None, "cli_version": "9.9.9",
        "tools_enabled": False,
    }


async def test_status_reports_whether_tools_are_on(db_session, ready_cli, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    async with client_for(local_app(db_session)) as client:
        status = await client.get("/api/v1/local/status")
    assert status.json()["tools_enabled"] is True


async def test_status_explains_a_signed_out_cli(db_session, monkeypatch):
    async def status():
        return local_claude_runner.CliStatus(
            installed=True, version="9.9.9", signed_in=False, error="Claude is not signed in."
        )

    monkeypatch.setattr(local_claude_runner, "get_status", status)
    async with client_for(local_app(db_session)) as client:
        body = (await client.get("/api/v1/local/status")).json()
    assert body["available"] is False
    assert body["reason"] == "Claude is not signed in."


async def test_signed_out_cli_creates_no_task(db_session, user_id, monkeypatch, no_background_run):
    async def status():
        return local_claude_runner.CliStatus(
            installed=True, signed_in=False, error="Claude is not signed in."
        )

    monkeypatch.setattr(local_claude_runner, "get_status", status)
    before = await task_count(db_session)
    async with client_for(local_app(db_session)) as client:
        created = await client.post(
            "/api/v1/local/tasks", json={"user_id": user_id, "prompt_text": "hi"}
        )
    assert created.status_code == 409
    assert created.json()["detail"] == "Claude is not signed in."
    assert await task_count(db_session) == before
    assert no_background_run == []


async def test_local_task_is_created_and_readable_only_through_local_routes(
    db_session, user_id, ready_cli, no_background_run
):
    async with client_for(local_app(db_session)) as client:
        created = await client.post(
            "/api/v1/local/tasks", json={"user_id": user_id, "prompt_text": "say hi"}
        )
        assert created.status_code == 200
        task = created.json()
        assert task["provider"] == "claude-local"
        assert task["status"] == "running"
        assert task["session_id"]
        await asyncio.sleep(0)  # let the scheduled background run start
        assert no_background_run == [task["id"]]

        assert (await client.get(f"/api/v1/local/tasks/{task['id']}")).json()["id"] == task["id"]
        listed = await client.get("/api/v1/local/tasks", params={"user_id": user_id})
        assert [t["id"] for t in listed.json()] == [task["id"]]

        # The legacy guest-id endpoints must not expose it.
        assert (await client.get(f"/api/v1/tasks/{task['id']}")).status_code == 404
        legacy = await client.get("/api/v1/tasks", params={"user_id": user_id})
        assert legacy.json() == []


async def test_local_routes_do_not_serve_boat_tasks(db_session, user_id, ready_cli):
    boat = Tasks(
        user_id=user_id, provider="claude", box_id="bx_abcdefgh",
        prompt_text="x", status="succeeded", session_id="s",
    )
    db_session.add(boat)
    await db_session.flush()
    async with client_for(local_app(db_session)) as client:
        response = await client.get(f"/api/v1/local/tasks/{boat.id}")
    assert response.status_code == 404


async def test_empty_prompt_is_rejected(db_session, user_id, ready_cli, no_background_run):
    async with client_for(local_app(db_session)) as client:
        created = await client.post(
            "/api/v1/local/tasks", json={"user_id": user_id, "prompt_text": ""}
        )
    assert created.status_code == 422


async def test_legacy_create_cannot_select_the_local_provider(db_session, user_id):
    before = await task_count(db_session)
    async with client_for(local_app(db_session)) as client:
        response = await client.post(
            "/api/v1/tasks",
            json={
                "user_id": user_id, "provider": "claude-local", "box_id": "local",
                "prompt_text": "hi",
            },
        )
    assert response.status_code == 400
    assert await task_count(db_session) == before


async def test_schedules_cannot_select_the_local_provider(db_session, user_id):
    with pytest.raises(InvalidScheduleError):
        await schedule_service.create_schedule(
            db_session, user_id=user_id, provider="claude-local", box_id="local",
            prompt_text="hi", cron_expression="0 8 * * *", timezone="UTC",
        )


async def test_service_hides_local_tasks_without_a_grant(db_session, user_id):
    local = Tasks(
        user_id=user_id, provider="claude-local", box_id="local",
        prompt_text="x", status="succeeded", session_id="s", result="private answer",
    )
    db_session.add(local)
    await db_session.flush()

    assert await task_execution_service.get_task(db_session, local.id) is None
    assert await task_execution_service.list_tasks_for_user(db_session, user_id) == []
    visible = await task_execution_service.get_task(db_session, local.id, access=_issue_grant())
    assert visible is not None and visible.result == "private answer"


async def test_execute_local_task_requires_a_real_grant(db_session, user_id):
    with pytest.raises(PermissionError):
        await task_execution_service.execute_local_task(
            db_session, access=object(), user_id=user_id, prompt_text="hi"
        )
    with pytest.raises(TypeError):
        LocalAccessGrant()


async def test_syncing_a_local_task_never_calls_boat(db_session, user_id, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("Boat must not be contacted for a local task")

    monkeypatch.setattr(task_execution_service.ascii_box_service, "command_status", boom)
    local = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="x",
        status="running", session_id="s", prompt_id="123",
    )
    db_session.add(local)
    await db_session.flush()
    synced = await task_execution_service.sync_task_status(db_session, local)
    assert synced.status == "running"
