"""The computers HTTP API, and how agents and conversations show their computer.
Real routes, runtime and database; the fake docker plays Docker."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from app.computers.provider import DockerProvider
from app.computers.service import ComputerService
from app.core.config import settings
from app.db.models import Agent, Computer
from app.routers.agents import get_runtime as agents_runtime
from app.routers.agents import router as agents_router
from app.routers.computers import get_runtime as computers_runtime
from app.routers.computers import router as computers_router
from app.routers.threads import get_runtime as threads_runtime
from app.routers.threads import router as threads_router
from tests.computers.harness import IMAGE, docker  # noqa: F401
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions  # noqa: F401


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture(autouse=True)
async def rows_cleanup(sessions, owner):  # noqa: F811
    yield
    async with sessions() as db:
        await db.execute(delete(Computer).where(Computer.owner_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


@pytest.fixture
async def api(rt, cli, docker, tmp_path):  # noqa: F811
    rt.computers = ComputerService(
        providers={"docker": DockerProvider(docker=str(docker.path))},
        owner_id=rt.owner_id,
        root=tmp_path / "computers",
        _session_factory=rt.session,
    )
    app = FastAPI()
    for router, dependency in ((agents_router, agents_runtime), (threads_router, threads_runtime), (computers_router, computers_runtime)):
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[dependency] = lambda: rt
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield client


async def new_agent(api):
    response = await api.post("/api/v1/agents", json={"name": "Agent " + uuid4().hex[:6]})
    assert response.status_code == 201, response.text
    return response.json()


async def test_browser_opt_in_is_desktop_only_and_active_turns_refuse_changes(api, rt):  # noqa: F811
    agent = await new_agent(api)
    created = await api.post(f"/api/v1/agents/{agent['id']}/computer", json={"kind": "desktop"})
    assert created.status_code == 201
    computer = created.json()
    assert computer["browser_enabled"] is False
    path = f"/api/v1/computers/{computer['id']}"
    assert (await api.patch(path, json={"browser_enabled": True})).json()["browser_enabled"] is True
    assert (await api.get(path, params={"live": "false"})).json()["browser_enabled"] is True
    rt.computers._leases[computer["id"]] = {999}
    try:
        response = await api.patch(path, json={"browser_enabled": False})
        assert response.status_code == 409
        assert response.json()["detail"]["error_code"] == "computer_busy"
    finally:
        rt.computers._leases.pop(computer["id"], None)
    assert (await api.patch(path, json={"browser_enabled": False})).json()["browser_enabled"] is False
    shell_agent = await new_agent(api)
    response = await api.post(f"/api/v1/agents/{shell_agent['id']}/computer", json={"kind": "shell", "browser_enabled": True})
    assert response.status_code == 422


async def test_health_reports_docker_and_degrades_when_it_is_down(api, docker):  # noqa: F811
    body = (await api.get("/api/v1/computers/health")).json()
    assert body == {"provider": "docker", "available": True, "version": "27.0.0-fake", "error": None}
    docker.mode("down")
    body = (await api.get("/api/v1/computers/health")).json()
    assert body["available"] is False and "not running" in body["error"]


async def test_an_agent_gets_one_computer_and_shows_it(api, docker):  # noqa: F811
    agent = await new_agent(api)
    assert agent["computer"] is None and agent["computer_id"] is None
    assert (await api.get(f"/api/v1/agents/{agent['id']}/computer")).status_code == 404

    created = await api.post(f"/api/v1/agents/{agent['id']}/computer", json={"cpus": 2, "memory_mb": 2048})
    assert created.status_code == 201, created.text
    computer = created.json()
    assert computer["state"] == "missing" and computer["image"] == IMAGE and computer["image_pinned"]
    assert computer["cpus"] == 2 and computer["network"] == "none" and computer["guest_workspace"] == "/workspace"
    assert computer["health"]["available"] is True and computer["live_state"] == "missing"

    again = await api.post(f"/api/v1/agents/{agent['id']}/computer", json={"cpus": 8})
    assert again.status_code == 200 and again.json()["id"] == computer["id"] and again.json()["cpus"] == 2

    shown = (await api.get(f"/api/v1/agents/{agent['id']}")).json()
    assert shown["computer_id"] == computer["id"] and shown["computer"]["state"] == "missing"
    listed = (await api.get("/api/v1/agents")).json()
    assert next(a for a in listed if a["id"] == agent["id"])["computer"]["name"] == computer["name"]

    thread = (await api.post("/api/v1/threads", json={"agent_id": agent["id"]})).json()
    assert thread["computer"]["id"] == computer["id"]
    assert (await api.get(f"/api/v1/threads/{thread['id']}")).json()["computer"]["id"] == computer["id"]
    plain = (await api.post("/api/v1/threads", json={})).json()
    assert plain["computer"] is None


async def test_lifecycle_through_the_api(api, docker):  # noqa: F811
    agent = await new_agent(api)
    computer = (await api.post(f"/api/v1/agents/{agent['id']}/computer", json={})).json()
    base = f"/api/v1/computers/{computer['id']}"

    refused = await api.post(f"{base}/start")
    assert refused.status_code == 409 and refused.json()["detail"]["error_code"] == "image_missing"
    prepared = await api.post(f"{base}/prepare-image")
    assert prepared.status_code == 200, prepared.text
    started = (await api.post(f"{base}/start")).json()
    assert started["state"] == "running" and started["live_state"] == "running" and started["container_id"]
    stopped = (await api.post(f"{base}/stop")).json()
    assert stopped["state"] == "stopped"

    changed = await api.patch(base, json={"network": "default", "memory_mb": 512})
    assert changed.status_code == 200 and changed.json()["needs_recreate"] is True
    recreated = (await api.post(f"{base}/recreate")).json()
    assert recreated["state"] == "running" and recreated["needs_recreate"] is False
    assert (await api.patch(base, json={})).status_code == 422
    assert (await api.patch(base, json={"network": "host"})).status_code == 422

    removed = await api.delete(base)
    assert removed.status_code == 200 and removed.json() == {"removed": True}
    assert (await api.get(base)).status_code == 404
    assert docker.state()["containers"] == {}
    assert (await api.get(f"/api/v1/agents/{agent['id']}")).json()["computer"] is None


async def test_a_folder_docker_cannot_mount_is_refused_and_not_left_creating(api, docker, tmp_path):  # noqa: F811
    docker.add_image()
    folder = tmp_path / "client a,b"
    folder.mkdir()
    made = await api.post("/api/v1/agents", json={"name": "Agent " + uuid4().hex[:6], "workspace_path": str(folder)})
    assert made.status_code == 201, made.text
    computer = (await api.post(f"/api/v1/agents/{made.json()['id']}/computer", json={})).json()
    started = await api.post(f"/api/v1/computers/{computer['id']}/start")
    assert started.status_code == 422 and started.json()["detail"]["error_code"] == "invalid_computer"
    assert "comma" in started.json()["detail"]["message"]
    shown = (await api.get(f"/api/v1/computers/{computer['id']}", params={"live": False})).json()
    assert shown["state"] == "error"


async def test_docker_down_answers_503_and_a_pending_removal_hides_the_computer(api, docker):  # noqa: F811
    docker.add_image()
    agent = await new_agent(api)
    computer = (await api.post(f"/api/v1/agents/{agent['id']}/computer", json={"start": True})).json()
    assert computer["state"] == "running"
    docker.mode("down")
    response = await api.post(f"/api/v1/computers/{computer['id']}/stop")
    assert response.status_code == 503 and response.json()["detail"]["error_code"] == "docker_unavailable"
    shown = (await api.get(f"/api/v1/computers/{computer['id']}")).json()
    assert shown["health"]["available"] is False and shown["live_state"] is None
    assert (await api.delete(f"/api/v1/computers/{computer['id']}")).json() == {"removed": False}
    assert (await api.get(f"/api/v1/computers/{computer['id']}")).status_code == 404
    assert (await api.get("/api/v1/computers")).json() == []


async def test_deleting_the_agent_removes_its_computer(api, docker):  # noqa: F811
    docker.add_image()
    agent = await new_agent(api)
    computer = (await api.post(f"/api/v1/agents/{agent['id']}/computer", json={"start": True})).json()
    assert (await api.delete(f"/api/v1/agents/{agent['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/computers/{computer['id']}")).status_code == 404
    assert docker.state()["containers"] == {}


async def test_remote_callers_are_refused(api, rt):  # noqa: F811
    app = api._transport.app
    transport = httpx.ASGITransport(app, client=("203.0.113.9", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://example.com") as remote:
        assert (await remote.get("/api/v1/computers/health")).status_code in (403, 404)
