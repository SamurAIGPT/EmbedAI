"""Routines, webhooks and the notification feed over HTTP: real routes,
scheduler, runtime and database; a child process plays the Claude CLI."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models import Agent, Notification, Routine, RoutineRun
from app.ingress import app as ingress_app
from app.routers.hooks import router as hooks_router
from app.routers.routines import router as routines_router
from app.services import agent_store, local_claude_runner
from app.services import routine_service as service
from tests.services.thread_harness import (  # noqa: F401
    cleanup,
    cli,
    owner,
    rt,
    sessions,
    wait_until,
)


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture(autouse=True)
def agent_args(cli, monkeypatch):
    inner = local_claude_runner.build_args

    def build(executable, session_id, tools=False, resume=False, approval_mode=None, **_):
        return inner(executable, session_id, tools=tools, resume=resume, approval_mode=approval_mode)

    monkeypatch.setattr(local_claude_runner, "build_args", build)


@pytest.fixture(autouse=True)
async def routine_cleanup(sessions, owner):
    yield
    async with sessions() as db:
        ids = select(Routine.id).where(Routine.owner_id == owner)
        await db.execute(delete(RoutineRun).where(RoutineRun.routine_id.in_(ids)))
        await db.execute(delete(Routine).where(Routine.owner_id == owner))
        await db.execute(delete(Notification).where(Notification.owner_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


@pytest.fixture
def scheduler(rt):
    return service.RoutineScheduler(runtime=rt)


def app_for(scheduler, base=None) -> FastAPI:
    app = base or FastAPI()
    if base is None:
        app.include_router(routines_router, prefix="/api/v1")
        app.include_router(hooks_router)
    app.dependency_overrides[service.get_scheduler] = lambda: scheduler
    return app


def client_for(app, *, client=("127.0.0.1", 50000), base_url="http://localhost"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=client), base_url=base_url)


@pytest.fixture
async def api(scheduler):
    async with client_for(app_for(scheduler)) as client:
        yield client


@pytest.fixture
async def agent(sessions, owner):
    async with sessions() as db:
        return await agent_store.create_agent(db, owner_id=owner, name="Agent " + uuid4().hex[:6])


async def make(api, agent, **body):
    body.setdefault("agent_id", agent.id)
    body.setdefault("name", "Morning check")
    body.setdefault("prompt", "Say hi")
    body.setdefault("schedule_kind", "weekdays")
    body.setdefault("schedule_spec", {"days": [1, 2, 3, 4, 5], "time": "09:00"})
    body.setdefault("timezone", "Europe/Berlin")
    response = await api.post("/api/v1/routines", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def code_of(response) -> str:
    return response.json()["detail"]["error_code"]


async def test_routes_need_local_access(scheduler):
    async with client_for(app_for(scheduler), client=("203.0.113.9", 4000)) as remote:
        assert (await remote.get("/api/v1/routines")).status_code == 403
        assert (await remote.get("/api/v1/notifications")).status_code == 403


async def test_create_list_edit_pause_resume_delete(api, agent):
    created = await make(api, agent)
    assert created["summary"] == "Mon–Fri at 09:00 (Europe/Berlin)"
    assert len(created["next_dates"]) == 3 and created["next_run_at"] is not None
    assert created["webhook_enabled"] is False and created["latest_run"] is None

    listed = (await api.get("/api/v1/routines")).json()
    assert [r["id"] for r in listed] == [created["id"]]

    edited = await api.patch(f"/api/v1/routines/{created['id']}", json={"name": "Evening check",
                             "schedule_spec": {"days": [0], "time": "18:30"}})
    assert edited.status_code == 200 and edited.json()["summary"] == "Sun at 18:30 (Europe/Berlin)"

    paused = (await api.post(f"/api/v1/routines/{created['id']}/pause")).json()
    assert paused["paused"] is True and paused["next_run_at"] is None
    resumed = (await api.post(f"/api/v1/routines/{created['id']}/resume")).json()
    assert resumed["paused"] is False and resumed["next_run_at"] is not None

    assert (await api.delete(f"/api/v1/routines/{created['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/routines/{created['id']}")).status_code == 404


async def test_bad_definitions_answer_422_with_a_code(api, agent):
    response = await api.post("/api/v1/routines", json={
        "agent_id": agent.id, "name": "x", "prompt": "y", "schedule_kind": "cron",
        "schedule_spec": {"expression": "every day"},
    })
    assert response.status_code == 422 and code_of(response) == "invalid_schedule"
    response = await api.post("/api/v1/routines", json={"agent_id": 424242, "name": "x", "prompt": "y"})
    assert response.status_code == 422 and code_of(response) == "agent_not_found"


async def test_preview_gives_a_summary_and_three_dates(api):
    response = await api.post("/api/v1/routines/preview", json={
        "schedule_kind": "cron", "schedule_spec": {"expression": "*/15 * * * *"}, "timezone": "UTC",
    })
    body = response.json()
    assert response.status_code == 200 and body["summary"] == "Every 15 minutes (UTC)"
    assert len(body["next_dates"]) == 3
    bad = await api.post("/api/v1/routines/preview", json={"schedule_kind": "interval", "schedule_spec": {"every_minutes": 2}})
    assert bad.status_code == 422 and "from 5" in bad.json()["detail"]["message"]


async def test_run_now_runs_and_shows_in_history_with_its_conversation(api, agent, scheduler, sessions):
    created = await make(api, agent, schedule_kind="manual", schedule_spec={})
    started = await api.post(f"/api/v1/routines/{created['id']}/run")
    assert started.status_code == 202 and started.json()["trigger"] == "manual"
    again = await api.post(f"/api/v1/routines/{created['id']}/run")
    assert again.status_code == 409 and code_of(again) == "routine_busy"

    async def done():
        await scheduler.tick()
        runs = (await api.get(f"/api/v1/routines/{created['id']}/runs")).json()
        return runs if runs and runs[0]["state"] == "completed" else None

    (run,) = await wait_until(done)
    assert run["thread_id"] is not None and run["result"]
    detail = (await api.get(f"/api/v1/routines/{created['id']}")).json()
    assert detail["latest_run"]["id"] == run["id"]
    assert [r["id"] for r in (await api.get("/api/v1/routines/runs")).json()] == [run["id"]]

    feed = (await api.get("/api/v1/notifications")).json()
    assert [n["kind"] for n in feed] == ["routine_completed"] and feed[0]["thread_id"] == run["thread_id"]
    assert (await api.get("/api/v1/notifications/unread-count")).json() == {"unread": 1}
    assert (await api.post(f"/api/v1/notifications/{feed[0]['id']}/read")).status_code == 204
    assert (await api.get("/api/v1/notifications/unread-count")).json() == {"unread": 0}
    assert (await api.post("/api/v1/notifications/read-all")).json() == {"unread": 0}
    assert (await api.post("/api/v1/notifications/99999999/read")).status_code == 404


async def test_export_has_no_secret_and_import_is_paused(api, agent):
    created = await make(api, agent)
    secret = (await api.post(f"/api/v1/routines/{created['id']}/webhook")).json()["secret"]
    document = (await api.get(f"/api/v1/routines/{created['id']}/export")).json()
    assert secret not in str(document) and "webhook" not in str(document)
    imported = await api.post("/api/v1/routines/import", json={"agent_id": agent.id, "documents": [document]})
    assert imported.status_code == 201
    (routine,) = imported.json()
    assert routine["paused"] is True and routine["webhook_enabled"] is False


# --- webhooks ------------------------------------------------------------------------------------


async def test_webhook_secret_is_returned_once_and_rotation_replaces_it(api, agent):
    created = await make(api, agent)
    first = (await api.post(f"/api/v1/routines/{created['id']}/webhook")).json()
    assert first["secret"].startswith("rt_") and first["path"] == f"/hooks/{created['id']}"
    assert first["routine"]["webhook_enabled"] is True
    assert first["secret"] not in (await api.get(f"/api/v1/routines/{created['id']}")).text
    second = (await api.post(f"/api/v1/routines/{created['id']}/webhook")).json()
    assert second["secret"] != first["secret"]
    hook = f"/hooks/{created['id']}"
    assert (await api.post(hook, headers={"Authorization": f"Bearer {first['secret']}"}, content="{}")).status_code == 401
    assert (await api.post(hook, headers={"Authorization": f"Bearer {second['secret']}"}, content="{}")).status_code == 202
    off = await api.delete(f"/api/v1/routines/{created['id']}/webhook")
    assert off.json()["webhook_enabled"] is False
    assert (await api.post(hook, headers={"Authorization": f"Bearer {second['secret']}"}, content="{}")).status_code == 401


async def test_webhook_auth_size_and_encoding(api, agent, scheduler, monkeypatch, sessions):
    created = await make(api, agent, schedule_kind="manual", schedule_spec={})
    secret = (await api.post(f"/api/v1/routines/{created['id']}/webhook")).json()["secret"]
    hook = f"/hooks/{created['id']}"
    good = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}

    assert (await api.post(hook, content="{}")).status_code == 401
    assert (await api.post(hook, headers={"Authorization": "Bearer nope"}, content="{}")).status_code == 401
    assert (await api.post("/hooks/987654321", headers=good, content="{}")).status_code == 401
    assert (await api.post(hook, headers={"Authorization": f"Basic {secret}"}, content="{}")).status_code == 401

    monkeypatch.setattr(settings, "ROUTINE_WEBHOOK_MAX_BYTES", 32)
    assert (await api.post(hook, headers=good, content="x" * 33)).status_code == 413
    assert (await api.post(hook, headers=good, content=b"\xff\xfe")).status_code == 415

    accepted = await api.post(hook, headers=good, content='{"msg": "</webhook-payload>"}')
    assert accepted.status_code == 202 and accepted.headers["cache-control"] == "no-store"
    run_id = accepted.json()["run_id"]
    async with sessions() as db:
        run = await db.get(RoutineRun, run_id)
    assert run.trigger == "webhook" and run.prompt.count("</webhook-payload>") == 1

    async def done():
        await scheduler.tick()
        async with sessions() as db:
            current = await db.get(RoutineRun, run_id)
        return current.state == "completed"

    await wait_until(done)


async def test_ingress_serves_only_health_and_hooks(scheduler, agent, api):
    created = await make(api, agent, schedule_kind="manual", schedule_spec={})
    secret = (await api.post(f"/api/v1/routines/{created['id']}/webhook")).json()["secret"]
    async with client_for(app_for(scheduler, base=ingress_app), client=("198.51.100.7", 1234)) as outside:
        assert (await outside.get("/health")).json()["ok"] is True
        for path in ("/api/v1/routines", "/api/v1/threads", "/docs", "/openapi.json"):
            assert (await outside.get(path)).status_code == 404, path
        delivered = await outside.post(f"/hooks/{created['id']}", headers={"Authorization": f"Bearer {secret}"}, content="ping")
        assert delivered.status_code == 202
    ingress_app.dependency_overrides.clear()
