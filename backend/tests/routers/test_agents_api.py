"""Named agents (F06): the HTTP API, and what a conversation started from an
agent runs with. Real routes, runtime and database; a real child process plays
the Claude CLI."""

import sys
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from app.core.config import settings
from app.db.models import Agent
from app.routers.agents import get_runtime as agents_runtime
from app.routers.agents import router as agents_router
from app.routers.threads import get_runtime as threads_runtime
from app.routers.threads import router as threads_router
from app.services import local_claude_runner
from tests.services.thread_harness import (  # noqa: F401
    FAKE_CLI,
    cleanup,
    cli,
    finished,
    owner,
    rt,
    sessions,
    wait_until,
)


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture(autouse=True)
async def agent_cleanup(sessions, owner):
    yield
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


def app_for(rt) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router, prefix="/api/v1")
    app.include_router(threads_router, prefix="/api/v1")
    app.dependency_overrides[agents_runtime] = lambda: rt
    app.dependency_overrides[threads_runtime] = lambda: rt
    return app


def client_for(app, *, client=("127.0.0.1", 50000), base_url="http://localhost"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=client), base_url=base_url)


@pytest.fixture
async def api(rt, cli):  # `cli` keeps every folder an agent gets under tmp_path
    async with client_for(app_for(rt)) as client:
        yield client


async def make(api, **body):
    body.setdefault("name", "Agent " + uuid4().hex[:6])
    response = await api.post("/api/v1/agents", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def code_of(response) -> str:
    return response.json()["detail"]["error_code"]


# --- who may use it ------------------------------------------------------------------------------

ROUTES = [
    ("POST", "/api/v1/agents", {"name": "x"}),
    ("GET", "/api/v1/agents", None),
    ("GET", "/api/v1/agents/1", None),
    ("PATCH", "/api/v1/agents/1", {"name": "y"}),
    ("POST", "/api/v1/agents/1/duplicate", None),
    ("DELETE", "/api/v1/agents/1", None),
    ("GET", "/api/v1/agents/1/memory", None),
    ("PUT", "/api/v1/agents/1/memory", {"text": "x"}),
    ("GET", "/api/v1/agents/1/export", None),
    ("POST", "/api/v1/agents/import", {"document": {}}),
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


# --- the agents API ------------------------------------------------------------------------------


async def test_an_agent_is_created_read_and_listed(api):
    agent = await make(
        api, name="Reviewer", title="Code reviewer", instructions="Be terse.", model="sonnet",
        approval_mode="plan", pinned=True,
    )
    assert (agent["name"], agent["title"], agent["provider"], agent["approval_mode"]) == (
        "Reviewer", "Code reviewer", "claude", "plan",
    )
    assert agent["workspace_path"] is None and agent["archived_at"] is None and agent["pinned"] is True
    assert "owner_id" not in agent
    assert (await api.get(f"/api/v1/agents/{agent['id']}")).json() == agent
    assert [a["id"] for a in (await api.get("/api/v1/agents")).json()] == [agent["id"]]


async def test_pinned_agents_list_first_then_by_name(api):
    b = await make(api, name="B")
    a = await make(api, name="A")
    z = await make(api, name="Z", pinned=True)
    assert [x["id"] for x in (await api.get("/api/v1/agents")).json()] == [z["id"], a["id"], b["id"]]


async def test_defaults_are_ask_claude_and_no_instructions(api):
    agent = await make(api)
    assert (agent["approval_mode"], agent["provider"], agent["instructions"], agent["pinned"]) == (
        "ask", "claude", "", False,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"name": "   "},
        {"name": "two\nlines"},
        {"name": "ok", "title": "two\nlines"},
        {"name": "ok", "approval_mode": "yolo"},
        {"name": "ok", "instructions": "é" * 12_001},  # over 24,000 bytes
        {"name": "ok", "provider": "nope"},
    ],
)
async def test_invalid_agents_are_refused(api, body):
    assert (await api.post("/api/v1/agents", json=body)).status_code == 422


async def test_a_workspace_must_be_an_absolute_existing_folder(api, tmp_path):
    relative = await api.post("/api/v1/agents", json={"name": "r", "workspace_path": "work"})
    assert relative.status_code == 422 and code_of(relative) == "workspace_unavailable"
    missing = await api.post("/api/v1/agents", json={"name": "m", "workspace_path": str(tmp_path / "nope")})
    assert missing.status_code == 422 and code_of(missing) == "workspace_unavailable"
    agent = await make(api, workspace_path=str(tmp_path))
    assert agent["workspace_path"] == str(tmp_path.resolve())


async def test_names_are_unique_among_live_agents_and_archiving_frees_one(api):
    first = await make(api, name="Dup")
    clash = await api.post("/api/v1/agents", json={"name": "Dup"})
    assert clash.status_code == 409 and code_of(clash) == "agent_name_taken"

    assert (await api.patch(f"/api/v1/agents/{first['id']}", json={"archived": True})).status_code == 200
    assert (await api.get("/api/v1/agents")).json() == []
    assert len((await api.get("/api/v1/agents", params={"include_archived": True})).json()) == 1
    second = await make(api, name="Dup")  # the name is free again

    clash_back = await api.patch(f"/api/v1/agents/{first['id']}", json={"archived": False})
    assert clash_back.status_code == 409 and code_of(clash_back) == "agent_name_taken"
    assert (await api.get(f"/api/v1/agents/{second['id']}")).status_code == 200


async def test_an_agent_is_edited_and_goes_back_to_defaults_on_null(api):
    agent = await make(api, name="Ed", model="opus", title="T", description="D")
    edited = (
        await api.patch(
            f"/api/v1/agents/{agent['id']}",
            json={"instructions": "New.", "approval_mode": "accept_edits", "model": None, "title": None},
        )
    ).json()
    assert (edited["instructions"], edited["approval_mode"], edited["model"], edited["title"]) == (
        "New.", "accept_edits", None, None,
    )
    assert edited["description"] == "D" and edited["name"] == "Ed"  # untouched fields stay


@pytest.mark.parametrize("body", [{}, {"name": None}, {"approval_mode": None}, {"approval_mode": "x"}, {"pinned": None}])
async def test_an_empty_or_null_patch_is_refused(api, body):
    agent = await make(api)
    assert (await api.patch(f"/api/v1/agents/{agent['id']}", json=body)).status_code == 422


async def test_an_unknown_agent_is_404_and_so_is_someone_elses(api, rt, sessions):
    assert (await api.get("/api/v1/agents/999999999")).status_code == 404
    async with sessions() as db:
        from app.services import agent_store

        theirs = await agent_store.create_agent(db, owner_id="someone-else-" + uuid4().hex, name="X")
    try:
        assert (await api.get(f"/api/v1/agents/{theirs.id}")).status_code == 404
        assert (await api.patch(f"/api/v1/agents/{theirs.id}", json={"name": "y"})).status_code == 404
        assert (await api.delete(f"/api/v1/agents/{theirs.id}")).status_code == 404
        assert (await api.post(f"/api/v1/agents/{theirs.id}/duplicate")).status_code == 404
    finally:
        async with sessions() as db:
            await db.execute(delete(Agent).where(Agent.id == theirs.id))
            await db.commit()


async def test_duplicating_copies_the_settings_under_a_new_name(api):
    source = await make(api, name="Base", instructions="I", model="m", approval_mode="plan", pinned=True)
    one = (await api.post(f"/api/v1/agents/{source['id']}/duplicate")).json()
    two = (await api.post(f"/api/v1/agents/{source['id']}/duplicate")).json()
    assert (one["name"], two["name"]) == ("Base copy", "Base copy 2")
    for copy in (one, two):
        assert (copy["instructions"], copy["model"], copy["approval_mode"]) == ("I", "m", "plan")
        assert copy["pinned"] is False and copy["id"] != source["id"]


async def test_a_copy_of_a_long_name_stays_within_the_name_limit(api):
    long = "N" * 76 + uuid4().hex[:4]
    source = await make(api, name=long)
    one = (await api.post(f"/api/v1/agents/{source['id']}/duplicate")).json()
    two = (await api.post(f"/api/v1/agents/{source['id']}/duplicate")).json()
    assert one["name"] == long[:75] + " copy" and two["name"] == long[:73] + " copy 2"
    # The copy can be renamed, exported and imported again like any agent.
    assert (await api.patch(f"/api/v1/agents/{one['id']}", json={"name": one["name"]})).status_code == 200
    document = (await api.get(f"/api/v1/agents/{one['id']}/export")).json()["document"]
    imported = await api.post("/api/v1/agents/import", json={"document": document})
    assert imported.status_code == 201, imported.text
    assert len(imported.json()["name"]) <= 80
    document = (await api.get(f"/api/v1/agents/{source['id']}/export")).json()["document"]
    again = (await api.post("/api/v1/agents/import", json={"document": document})).json()
    assert again["name"] == long[:78] + " 2"


async def test_deleting_an_agent_removes_it(api):
    agent = await make(api)
    assert (await api.delete(f"/api/v1/agents/{agent['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/agents/{agent['id']}")).status_code == 404


# --- starting a conversation from an agent ------------------------------------------------------------


async def thread_from(api, agent, **body):
    return await api.post("/api/v1/threads", json={"agent_id": agent["id"], **body})


async def test_a_thread_without_a_chosen_folder_gets_the_agents_own_folder(api, tmp_path):
    agent = await make(api)
    thread = (await thread_from(api, agent)).json()
    expected = tmp_path / "agents" / str(agent["id"])
    assert thread["agent_id"] == agent["id"] and thread["provider"] == "claude"
    assert thread["workspace_path"] == str(expected.resolve()) and expected.is_dir()


async def test_a_thread_pins_the_agents_chosen_folder_and_later_edits_do_not_move_it(api, tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir(), second.mkdir()
    agent = await make(api, workspace_path=str(first))
    thread = (await thread_from(api, agent)).json()
    assert thread["workspace_path"] == str(first.resolve())
    await api.patch(f"/api/v1/agents/{agent['id']}", json={"workspace_path": str(second)})
    again = (await api.get(f"/api/v1/threads/{thread['id']}")).json()
    assert again["workspace_path"] == str(first.resolve())
    assert (await thread_from(api, agent)).json()["workspace_path"] == str(second.resolve())


async def test_a_thread_from_an_agent_refuses_bad_requests(api):
    agent = await make(api)
    assert code_of(await api.post("/api/v1/threads", json={"agent_id": 999999999})) == "agent_not_found"
    mismatch = await thread_from(api, agent, provider="other")
    assert mismatch.status_code == 422
    await api.patch(f"/api/v1/agents/{agent['id']}", json={"archived": True})
    archived = await thread_from(api, agent)
    assert archived.status_code == 409 and code_of(archived) == "agent_archived"


async def test_a_thread_without_an_agent_is_unchanged(api):
    thread = (await api.post("/api/v1/threads", json={"provider": "claude"})).json()
    assert thread["agent_id"] is None and thread["workspace_path"]


# --- what a turn runs with -------------------------------------------------------------------------------


@pytest.fixture
def launches(cli, monkeypatch):
    """Record every `build_args` call's settings while still launching the fake CLI."""
    seen: list[dict] = []

    def fake_build_args(
        executable, session_id, tools=False, resume=False, approval_mode=None, model=None, system_prompt=None, **_extra
    ):
        seen.append(
            {"tools": tools, "approval_mode": approval_mode, "model": model, "system_prompt": system_prompt}
        )
        return [
            sys.executable, str(FAKE_CLI), str(cli.plan),
            "resume" if resume else "new", session_id, "1" if tools else "0",
        ]

    monkeypatch.setattr(local_claude_runner, "build_args", fake_build_args)
    return seen


async def send(api, rt, sessions, thread_id, count):
    await rt.send_turn(thread_id, "hello")
    await finished(sessions, thread_id, count)


async def test_each_turn_uses_the_agents_current_settings(api, rt, sessions, launches):
    agent = await make(api, instructions="Answer in French.", model="sonnet", approval_mode="plan")
    thread = (await thread_from(api, agent)).json()
    await send(api, rt, sessions, thread["id"], 1)
    assert launches[-1]["system_prompt"].startswith("Answer in French.\n\n")
    assert launches[-1]["model"] == "sonnet"

    await api.patch(f"/api/v1/agents/{agent['id']}", json={"instructions": "Answer in German.", "model": "opus"})
    await send(api, rt, sessions, thread["id"], 2)
    assert launches[-1]["system_prompt"].startswith("Answer in German.\n\n")
    assert launches[-1]["model"] == "opus"


async def test_the_agents_approval_mode_is_on_every_turns_context(api, rt, sessions, launches, monkeypatch):
    seen = []
    original = rt.adapters["claude"].run_turn

    async def spy(ctx, sink, gate):
        seen.append((ctx.approval_mode, ctx.instructions))
        return await original(ctx, sink, gate)

    monkeypatch.setattr(rt.adapters["claude"], "run_turn", spy)
    agent = await make(api, approval_mode="accept_edits", instructions="Hi.", memory_enabled=False)
    thread = (await thread_from(api, agent)).json()
    await send(api, rt, sessions, thread["id"], 1)
    assert seen == [("accept_edits", "Hi.")]


async def test_a_model_chosen_for_the_conversation_wins_over_the_agents(api, rt, sessions, launches):
    agent = await make(api, model="sonnet")
    thread = (await thread_from(api, agent, model="haiku")).json()
    await send(api, rt, sessions, thread["id"], 1)
    assert launches[-1]["model"] == "haiku"


async def test_a_plain_thread_launches_without_agent_settings(api, rt, sessions, launches):
    thread = (await api.post("/api/v1/threads", json={"provider": "claude"})).json()
    await send(api, rt, sessions, thread["id"], 1)
    assert launches[-1]["model"] is None and launches[-1]["system_prompt"] is None
    assert launches[-1]["approval_mode"] is None


async def test_deleting_an_agent_unlinks_its_threads_and_they_keep_working(api, rt, sessions, launches):
    agent = await make(api, instructions="Special.")
    thread = (await thread_from(api, agent)).json()
    await send(api, rt, sessions, thread["id"], 1)
    assert (await api.delete(f"/api/v1/agents/{agent['id']}")).status_code == 204

    unlinked = (await api.get(f"/api/v1/threads/{thread['id']}")).json()
    assert unlinked["agent_id"] is None and unlinked["workspace_path"] == thread["workspace_path"]
    await send(api, rt, sessions, thread["id"], 2)
    assert launches[-1]["system_prompt"] is None
    assert len((await api.get(f"/api/v1/threads/{thread['id']}")).json()["turns"]) == 2


async def test_an_archived_agent_still_serves_its_existing_threads(api, rt, sessions, launches):
    agent = await make(api, instructions="Still here.")
    thread = (await thread_from(api, agent)).json()
    await api.patch(f"/api/v1/agents/{agent['id']}", json={"archived": True})
    await send(api, rt, sessions, thread["id"], 1)
    assert launches[-1]["system_prompt"].startswith("Still here.")


async def test_the_runner_adds_the_model_and_instructions_to_the_cli_arguments():
    from pathlib import Path

    args = local_claude_runner.build_args(
        Path("claude"), "sid", model="sonnet", system_prompt="Be brief."
    )
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--append-system-prompt") + 1] == "Be brief."
    assert "--system-prompt" not in args  # appended to the CLI's prompt, never replacing it
    plain = local_claude_runner.build_args(Path("claude"), "sid")
    assert "--model" not in plain and "--append-system-prompt" not in plain


# --- fallback engine ---------------------------------------------------------------------------------


async def test_an_agent_may_name_a_fallback_engine_and_it_is_checked(api):
    agent = await make(api)
    assert agent["fallback_provider"] is None and agent["fallback_model"] is None
    same = await api.patch(f"/api/v1/agents/{agent['id']}", json={"fallback_provider": "claude"})
    assert same.status_code == 422 and code_of(same) == "invalid_fallback"
    unknown = await api.patch(f"/api/v1/agents/{agent['id']}", json={"fallback_provider": "nope"})
    assert unknown.status_code == 422 and code_of(unknown) == "unknown_provider"
    orphan = await api.patch(f"/api/v1/agents/{agent['id']}", json={"fallback_model": "x"})
    assert orphan.status_code == 422 and code_of(orphan) == "invalid_fallback"
