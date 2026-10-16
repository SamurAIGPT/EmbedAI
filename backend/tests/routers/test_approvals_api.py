"""The decision log and saved-rule HTTP routes, and `remember` on an answer."""

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers.approval_rules import router as rules_router
from app.routers.decisions import router as decisions_router
from app.routers.threads import get_runtime
from app.routers.threads import router as threads_router
from app.services import decision_log
from tests.services.thread_harness import (  # noqa: F401
    cleanup,
    cli,
    finished,
    new_thread,
    owner,
    pending_approval,
    rt,
    sessions,
    tools_on,
)

BASH = {"type": "tool", "tool_name": "Bash", "tool_input": {"command": "git status -s"}}


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


def app_for(rt) -> FastAPI:
    app = FastAPI()
    for router in (threads_router, rules_router, decisions_router):
        app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    return app


def client_for(app, *, client=("127.0.0.1", 50000), base_url="http://localhost"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=client), base_url=base_url)


@pytest.fixture
async def api(rt):
    async with client_for(app_for(rt)) as client:
        yield client


ROUTES = [
    ("GET", "/api/v1/decisions", None),
    ("GET", "/api/v1/approval-rules", None),
    ("POST", "/api/v1/approval-rules", {"match": "exact", "command": "ls"}),
    ("DELETE", "/api/v1/approval-rules/1", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_every_route_refuses_anyone_but_this_machine(rt, method, path, body):
    app = app_for(rt)
    for kwargs in (dict(client=("203.0.113.9", 50000)), dict(base_url="http://evil.example")):
        async with client_for(app, **kwargs) as remote:
            assert (await remote.request(method, path, json=body)).status_code == 403


@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_every_route_refuses_everyone_when_local_execution_is_off(rt, monkeypatch, method, path, body):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", False)
    async with client_for(app_for(rt)) as client:
        assert (await client.request(method, path, json=body)).status_code == 403


async def test_decisions_are_read_only(api):
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert (await api.request(method, "/api/v1/decisions")).status_code == 405


async def test_decisions_page_newest_first_with_a_cursor_and_filters(api):
    for index in range(5):
        decision_log.record("shown" if index % 2 == 0 else "denied", thread_id=index % 2, approval_id=f"a{index}")
    first = (await api.get("/api/v1/decisions", params={"limit": 2})).json()
    assert [i["approval_id"] for i in first["items"]] == ["a4", "a3"] and first["next_before"]
    second = (await api.get("/api/v1/decisions", params={"limit": 2, "before": first["next_before"]})).json()
    assert [i["approval_id"] for i in second["items"]] == ["a2", "a1"]
    last = (await api.get("/api/v1/decisions", params={"limit": 2, "before": second["next_before"]})).json()
    assert [i["approval_id"] for i in last["items"]] == ["a0"] and last["next_before"] is None
    denied = (await api.get("/api/v1/decisions", params={"event": "denied"})).json()
    assert {i["event"] for i in denied["items"]} == {"denied"}
    assert len((await api.get("/api/v1/decisions", params={"thread_id": 1})).json()["items"]) == 2


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1000}, {"event": "approved"}])
async def test_bad_decision_queries_are_422(api, params):
    assert (await api.get("/api/v1/decisions", params=params)).status_code == 422


async def test_an_empty_log_is_an_empty_page(api):
    assert (await api.get("/api/v1/decisions")).json() == {"items": [], "next_before": None}


async def test_rules_are_added_listed_and_deleted(api):
    created = await api.post("/api/v1/approval-rules", json={"match": "prefix", "command": "git status"})
    assert created.status_code == 201
    rule = created.json()
    assert (rule["match"], rule["command"]) == ("prefix", "git status")
    again = await api.post("/api/v1/approval-rules", json={"match": "prefix", "command": "git status"})
    assert again.json()["id"] == rule["id"]
    assert [r["id"] for r in (await api.get("/api/v1/approval-rules")).json()] == [rule["id"]]
    assert (await api.delete(f"/api/v1/approval-rules/{rule['id']}")).status_code == 204
    assert (await api.delete(f"/api/v1/approval-rules/{rule['id']}")).status_code == 404
    assert (await api.get("/api/v1/approval-rules")).json() == []


async def test_a_rule_can_be_tied_to_a_folder(api):
    created = await api.post("/api/v1/approval-rules", json={"command": "ls", "workspace": "/work/a"})
    assert created.status_code == 201 and created.json()["workspace"] == "/work/a"
    other = await api.post("/api/v1/approval-rules", json={"command": "ls"})
    assert other.json()["id"] != created.json()["id"] and other.json()["workspace"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"command": "ls", "workspace": "relative"},
        {"command": "ls", "workspace": "/a/../b"},
        {"command": "git push --force"},
        {"command": "rm -rf /"},
        {"command": "export API_KEY=hunter2"},
        {"match": "prefix", "command": "ls; rm -rf ~"},
        {"match": "regex", "command": "ls"},
        {"match": "exact", "command": ""},
        {"match": "exact", "command": "x" * 1001},
    ],
)
async def test_a_bad_rule_is_a_422(api, body):
    assert (await api.post("/api/v1/approval-rules", json=body)).status_code == 422
    assert (await api.get("/api/v1/approval-rules")).json() == []


async def test_answering_with_remember_saves_a_rule_the_api_can_list(api, rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    approval = await pending_approval(sessions, thread.id)
    answered = await api.post(
        f"/api/v1/threads/{thread.id}/approvals/{approval.id}", json={"decision": "allow", "remember": True}
    )
    assert answered.status_code == 200
    await finished(sessions, thread.id)
    [rule] = (await api.get("/api/v1/approval-rules")).json()
    assert (rule["match"], rule["command"]) == ("exact", "git status -s")
    assert rule["workspace"]  # tied to the conversation's folder
    events = [i["event"] for i in (await api.get("/api/v1/decisions")).json()["items"]]
    assert events == ["allowed", "shown"]


async def test_remember_defaults_to_off(api, rt, sessions, cli, tools_on, owner):
    thread = await new_thread(sessions, owner)
    cli.script(BASH)
    await rt.send_turn(thread.id, "status")
    approval = await pending_approval(sessions, thread.id)
    assert (await api.post(f"/api/v1/threads/{thread.id}/approvals/{approval.id}", json={"decision": "allow"})).status_code == 200
    await finished(sessions, thread.id)
    assert (await api.get("/api/v1/approval-rules")).json() == []
