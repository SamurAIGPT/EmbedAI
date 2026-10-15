"""The threads HTTP API (F04, slice 3), end to end.

Real routes, a real runtime and database, and a real child process playing the
Claude CLI. The one thing not exercised over HTTP is the endless SSE body, which
the in-process test transport cannot read; its generator has its own tests
(tests/services/test_thread_stream.py) and the route's wiring is checked here."""

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers import threads as threads_module
from app.routers.threads import get_runtime
from app.routers.threads import router as threads_router
from app.services import thread_store as store
from app.services.claude_adapter import ClaudeAdapter
from app.services.provider_adapter import ProviderStatus
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.thread_harness import (  # noqa: F401
    TOOL_INPUT,
    cleanup,
    cli,
    new_thread,
    owner,
    rt,
    sessions,
    tools_on,
    wait_until,
)

TERMINAL = {"turn.completed", "turn.failed", "turn.cancelled", "turn.interrupted"}


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


def app_for(rt) -> FastAPI:
    app = FastAPI()
    app.include_router(threads_router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    return app


def client_for(app, *, client=("127.0.0.1", 50000), base_url="http://localhost"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=client), base_url=base_url)


@pytest.fixture
async def api(rt):
    async with client_for(app_for(rt)) as client:
        yield client


async def create(api, **body):
    response = await api.post("/api/v1/threads", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def events(api, thread_id, after=0, limit=200):
    response = await api.get(f"/api/v1/threads/{thread_id}/events", params={"after": after, "limit": limit})
    assert response.status_code == 200, response.text
    return response.json()


async def wait_for_terminals(api, thread_id, count=1):
    async def look():
        page = await events(api, thread_id)
        return len([e for e in page["events"] if e["type"] in TERMINAL]) >= count

    await wait_until(look)


async def pending(api, thread_id):
    async def look():
        response = await api.get(f"/api/v1/threads/{thread_id}/approvals", params={"status": "pending"})
        return response.json() or None

    return (await wait_until(look))[0]


def code_of(response) -> str:
    return response.json()["detail"]["error_code"]


# --- who may use it ------------------------------------------------------------------------------

ROUTES = [
    ("POST", "/api/v1/threads", {"provider": "claude"}),
    ("GET", "/api/v1/threads", None),
    ("GET", "/api/v1/threads/1", None),
    ("POST", "/api/v1/threads/1/turns", {"prompt_text": "hi"}),
    ("POST", "/api/v1/threads/1/session/reset", None),
    ("GET", "/api/v1/threads/1/events", None),
    ("GET", "/api/v1/threads/1/events/stream", None),
    ("GET", "/api/v1/threads/1/approvals", None),
    ("POST", "/api/v1/threads/1/approvals/abc", {"decision": "allow"}),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_every_route_refuses_anyone_but_this_machine(rt, method, path, body):
    app = app_for(rt)
    for kwargs, why in [
        (dict(client=("203.0.113.9", 50000)), "a remote client"),
        (dict(base_url="http://evil.example"), "an untrusted Host"),
    ]:
        async with client_for(app, **kwargs) as remote:
            response = await remote.request(method, path, json=body)
            assert response.status_code == 403, f"{method} {path} allowed {why}"


@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_every_route_refuses_everyone_when_local_execution_is_off(rt, monkeypatch, method, path, body):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", False)
    async with client_for(app_for(rt)) as client:
        assert (await client.request(method, path, json=body)).status_code == 403


async def test_a_cross_site_browser_request_is_refused(api):
    response = await api.post(
        "/api/v1/threads", json={"provider": "claude"}, headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403


# --- threads ------------------------------------------------------------------------------------


async def test_a_thread_is_created_listed_and_read(api, owner):
    thread = await create(api, model="sonnet", title="Notes")
    assert (thread["provider"], thread["model"], thread["title"], thread["session_state"]) == (
        "claude", "sonnet", "Notes", "new"
    )
    assert thread["last_seq"] == 0 and thread["archived_at"] is None
    assert "provider_session_id" not in thread and "owner_id" not in thread  # internals stay internal

    listed = (await api.get("/api/v1/threads")).json()
    assert [t["id"] for t in listed] == [thread["id"]]
    detail = (await api.get(f"/api/v1/threads/{thread['id']}")).json()
    assert detail["turns"] == [] and detail["active_turn_id"] is None


async def test_provider_defaults_to_claude_and_an_unknown_one_is_a_422(api):
    assert (await create(api))["provider"] == "claude"
    bad = await api.post("/api/v1/threads", json={"provider": "does-not-exist"})
    assert bad.status_code == 422 and "does-not-exist" in bad.json()["detail"]
    assert (await api.post("/api/v1/threads", json={"provider": ""})).status_code == 422


async def test_threads_list_newest_activity_first(api, sessions):
    older, newer = await create(api, title="older"), await create(api, title="newer")
    async with sessions() as db:
        await store.append_event(db, older["id"], "note", {})  # activity moves it up
    assert [t["title"] for t in (await api.get("/api/v1/threads")).json()] == ["older", "newer"]
    assert len((await api.get("/api/v1/threads", params={"limit": 1})).json()) == 1
    assert (await api.get("/api/v1/threads", params={"limit": 0})).status_code == 422


async def test_another_owners_threads_do_not_exist_here(api, sessions):
    theirs = await new_thread(sessions, "someone-else-" + "x", title="not yours")
    try:
        assert (await api.get(f"/api/v1/threads/{theirs.id}")).status_code == 404
        assert (await api.get(f"/api/v1/threads/{theirs.id}/events")).status_code == 404
        assert (await api.get(f"/api/v1/threads/{theirs.id}/events/stream")).status_code == 404
        assert (await api.post(f"/api/v1/threads/{theirs.id}/turns", json={"prompt_text": "hi"})).status_code == 404
        assert (await api.post(f"/api/v1/threads/{theirs.id}/session/reset")).status_code == 404
        assert (await api.get(f"/api/v1/threads/{theirs.id}/approvals")).status_code == 404
        assert (await api.get("/api/v1/threads")).json() == []
    finally:
        from sqlalchemy import delete

        from app.db.models import Thread

        async with sessions() as db:
            await db.execute(delete(Thread).where(Thread.id == theirs.id))
            await db.commit()


@pytest.mark.parametrize(
    "method,path,body",
    [(m, p.replace("/1", "/987654"), b) for m, p, b in ROUTES if "/1" in p],
)
async def test_an_unknown_thread_is_a_404_on_every_route(api, method, path, body):
    assert (await api.request(method, path, json=body)).status_code == 404


# --- sending a message ----------------------------------------------------------------------------------


async def test_a_message_runs_and_its_answer_arrives_as_stored_events(api, cli):
    thread = await create(api)
    sent = await api.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "hi there"})
    assert sent.status_code == 202
    turn = sent.json()
    assert (turn["seq"], turn["status"], turn["prompt_text"]) == (1, "running", "hi there")
    assert "owner_token" not in turn and "owner_instance" not in turn  # what authorizes a run is never shown

    await wait_for_terminals(api, thread["id"])
    page = await events(api, thread["id"])
    assert [e["type"] for e in page["events"]] == ["turn.started", "assistant.message", "turn.completed"]
    assert [e["seq"] for e in page["events"]] == [1, 2, 3]
    assert page["events"][1]["data"] == {"text": "Hello world"}

    detail = (await api.get(f"/api/v1/threads/{thread['id']}")).json()
    assert detail["title"] == "hi there" and detail["session_state"] == "active"
    assert detail["last_seq"] == 3 and detail["active_turn_id"] is None
    [done] = detail["turns"]
    assert (done["status"], done["result"], done["model"], done["error"]) == (
        "succeeded", "Hello world", "fake-model", None
    )


async def test_a_second_message_continues_the_same_session(api, cli):
    thread = await create(api)
    cli.script({"type": "chat", "text": "one"}, {"type": "chat", "text": "two"})
    for prompt in ("first", "second"):
        assert (await api.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": prompt})).status_code == 202
        await wait_for_terminals(api, thread["id"], 1 if prompt == "first" else 2)
    assert [mode for mode, _ in cli.launches()] == ["new", "resume"]
    assert len({sid for _, sid in cli.launches()}) == 1


async def test_a_blank_or_oversized_message_is_a_422_and_starts_nothing(api, sessions):
    thread = await create(api)
    for body in ({"prompt_text": ""}, {"prompt_text": "   \n "}, {"prompt_text": "x" * 100_001}, {}):
        assert (await api.post(f"/api/v1/threads/{thread['id']}/turns", json=body)).status_code == 422
    async with sessions() as db:
        assert await store.list_turns(db, thread["id"]) == []


async def test_a_message_while_a_turn_runs_is_a_409_with_a_code(api, cli, tools_on):
    thread = await create(api)
    cli.script({"type": "tool"})
    await api.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "first"})
    approval = await pending(api, thread["id"])
    busy = await api.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "second"})
    assert busy.status_code == 409 and code_of(busy) == "turn_running"
    detail = (await api.get(f"/api/v1/threads/{thread['id']}")).json()
    assert detail["active_turn_id"] == detail["turns"][0]["id"]
    await api.post(f"/api/v1/threads/{thread['id']}/approvals/{approval['id']}", json={"decision": "deny"})
    await wait_for_terminals(api, thread["id"])


async def test_a_provider_that_is_not_ready_is_a_503_and_starts_nothing(sessions, owner, cli):
    class NotReady(ClaudeAdapter):
        async def status(self):
            return ProviderStatus(ready=False, error="Claude is not signed in.")

    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"claude": NotReady()}, status_ttl=0, _session_factory=sessions, owner_id=owner
    )
    async with client_for(app_for(runtime)) as api:
        thread = await create(api)
        response = await api.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "hi"})
        assert response.status_code == 503 and code_of(response) == "provider_unavailable"
        assert "not signed in" in response.json()["detail"]["message"]
        assert (await api.get(f"/api/v1/threads/{thread['id']}")).json()["turns"] == []


# --- a broken session and the way out ---------------------------------------------------------------------


async def test_a_lost_session_breaks_the_thread_and_a_reset_is_the_way_out(api, cli, sessions, owner):
    thread = await new_thread(sessions, owner, session_state="active")  # a session the CLI no longer has
    tid = thread.id
    await api.post(f"/api/v1/threads/{tid}/turns", json={"prompt_text": "carry on"})
    await wait_for_terminals(api, tid)

    detail = (await api.get(f"/api/v1/threads/{tid}")).json()
    assert detail["session_state"] == "broken"
    assert (detail["turns"][0]["status"], detail["turns"][0]["error_code"]) == ("failed", "session_resume_failed")

    refused = await api.post(f"/api/v1/threads/{tid}/turns", json={"prompt_text": "again"})
    assert refused.status_code == 409 and code_of(refused) == "session_broken"
    assert len((await api.get(f"/api/v1/threads/{tid}")).json()["turns"]) == 1

    reset = await api.post(f"/api/v1/threads/{tid}/session/reset")
    assert reset.status_code == 200 and reset.json()["session_state"] == "new"
    page = await events(api, tid)
    last = page["events"][-1]
    assert last["type"] == "session.reset" and last["turn_id"] is None
    assert last["data"]["previous_state"] == "broken" and "transcript" in last["data"]["message"]
    assert page["events"][0]["type"] == "turn.started"  # the stored transcript is still there

    cli.script({"type": "chat", "text": "fresh start"})
    assert (await api.post(f"/api/v1/threads/{tid}/turns", json={"prompt_text": "hello again"})).status_code == 202
    await wait_for_terminals(api, tid, 2)
    final = (await api.get(f"/api/v1/threads/{tid}")).json()
    assert (final["session_state"], final["turns"][-1]["status"]) == ("active", "succeeded")
    # The retry used a new session, not the lost one.
    assert cli.launches()[-1][1] != thread.provider_session_id


async def test_a_session_cannot_be_reset_while_a_turn_runs(api, cli, tools_on):
    thread = await create(api)
    cli.script({"type": "tool"})
    await api.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "first"})
    approval = await pending(api, thread["id"])
    busy = await api.post(f"/api/v1/threads/{thread['id']}/session/reset")
    assert busy.status_code == 409 and code_of(busy) == "turn_running"
    await api.post(f"/api/v1/threads/{thread['id']}/approvals/{approval['id']}", json={"decision": "deny"})
    await wait_for_terminals(api, thread["id"])


async def test_a_provider_without_sessions_has_nothing_to_reset(sessions, owner):
    class Sessionless(ClaudeAdapter):
        key = "sessionless"

        def new_session_id(self):
            return None

        async def status(self):
            return ProviderStatus(ready=True)

    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"sessionless": Sessionless()}, status_ttl=0, _session_factory=sessions, owner_id=owner
    )
    async with client_for(app_for(runtime)) as api:
        thread = await create(api, provider="sessionless")
        assert thread["session_state"] == "none"
        refused = await api.post(f"/api/v1/threads/{thread['id']}/session/reset")
        assert refused.status_code == 409 and code_of(refused) == "no_session"


# --- reading events ------------------------------------------------------------------------------------


async def test_events_are_paged_with_a_cursor(api, sessions):
    thread = await create(api)
    for i in range(7):
        async with sessions() as db:
            await store.append_event(db, thread["id"], "note", {"i": i})
    first = await events(api, thread["id"], limit=3)
    assert ([e["seq"] for e in first["events"]], first["next_after"], first["has_more"]) == ([1, 2, 3], 3, True)
    second = await events(api, thread["id"], after=first["next_after"], limit=3)
    assert ([e["seq"] for e in second["events"]], second["has_more"]) == ([4, 5, 6], True)
    last = await events(api, thread["id"], after=second["next_after"], limit=3)
    assert ([e["seq"] for e in last["events"]], last["next_after"], last["has_more"]) == ([7], 7, False)
    empty = await events(api, thread["id"], after=7)
    assert (empty["events"], empty["next_after"], empty["has_more"]) == ([], 7, False)
    for params in ({"after": -1}, {"limit": 0}, {"limit": 1001}):
        assert (await api.get(f"/api/v1/threads/{thread['id']}/events", params=params)).status_code == 422


# --- the stream route (wiring only; see the module docstring) -------------------------------------------


async def test_the_stream_route_passes_the_cursor_and_the_right_headers(rt, monkeypatch):
    seen = {}

    async def fake_frames(thread_id, *, cursor, hub, session_factory, **options):
        seen.update(thread_id=thread_id, cursor=cursor, hub=hub, factory=session_factory)
        yield "id: 1\nevent: note\ndata: {}\n\n"

    monkeypatch.setattr(threads_module, "event_frames", fake_frames)
    async with client_for(app_for(rt)) as api:
        thread = await create(api)
        path = f"/api/v1/threads/{thread['id']}/events/stream"

        response = await api.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache, no-transform"
        assert response.text == "id: 1\nevent: note\ndata: {}\n\n"
        assert (seen["thread_id"], seen["cursor"], seen["hub"]) == (thread["id"], 0, rt.hub)
        assert seen["factory"] == rt.session

        await api.get(path, params={"after": 5})
        assert seen["cursor"] == 5
        await api.get(path, params={"after": 5}, headers={"Last-Event-ID": "9"})
        assert seen["cursor"] == 9  # a browser's own reconnect header wins
        await api.get(path, headers={"Last-Event-ID": " "})
        assert seen["cursor"] == 0

        for bad in ("abc", "-3", "1.5"):
            assert (await api.get(path, headers={"Last-Event-ID": bad})).status_code == 400


# --- approvals ------------------------------------------------------------------------------------------


async def test_an_approval_is_listed_answered_once_and_runs_exactly_what_was_shown(api, cli, tools_on):
    thread = await create(api)
    tid = thread["id"]
    cli.script({"type": "tool"})
    await api.post(f"/api/v1/threads/{tid}/turns", json={"prompt_text": "write a note"})

    approval = await pending(api, tid)
    assert (approval["tool"], approval["kind"], approval["input"], approval["status"]) == (
        "Write", "file_write", TOOL_INPUT, "pending"
    )
    assert cli.log("reply") == []  # nothing runs until a person answers

    answer = await api.post(f"/api/v1/threads/{tid}/approvals/{approval['id']}", json={"decision": "allow"})
    assert answer.status_code == 200 and answer.json() == {"status": "recorded", "decision": "allow"}
    await wait_for_terminals(api, tid)
    [reply] = cli.log("reply")
    assert reply["reply"]["response"]["response"] == {"behavior": "allow", "updatedInput": TOOL_INPUT}

    again = await api.post(f"/api/v1/threads/{tid}/approvals/{approval['id']}", json={"decision": "deny"})
    assert again.status_code == 409 and code_of(again) == "approval_not_waiting"
    assert [a["status"] for a in (await api.get(f"/api/v1/threads/{tid}/approvals")).json()] == ["allowed"]
    assert (await api.get(f"/api/v1/threads/{tid}/approvals", params={"status": "pending"})).json() == []


async def test_a_denied_call_is_refused(api, cli, tools_on):
    thread = await create(api)
    cli.script({"type": "tool"})
    await api.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "write a note"})
    approval = await pending(api, thread["id"])
    assert (
        await api.post(f"/api/v1/threads/{thread['id']}/approvals/{approval['id']}", json={"decision": "deny"})
    ).status_code == 200
    await wait_for_terminals(api, thread["id"])
    assert cli.log("reply")[0]["reply"]["response"]["response"]["behavior"] == "deny"


async def test_approval_requests_for_other_threads_or_with_bad_input_are_refused(api, cli, tools_on):
    one, two = await create(api), await create(api)
    cli.script({"type": "tool"})
    await api.post(f"/api/v1/threads/{one['id']}/turns", json={"prompt_text": "write a note"})
    approval = await pending(api, one["id"])

    wrong_thread = await api.post(f"/api/v1/threads/{two['id']}/approvals/{approval['id']}", json={"decision": "allow"})
    assert wrong_thread.status_code == 404  # answered through the wrong thread: no
    assert (await api.post(f"/api/v1/threads/{one['id']}/approvals/nope", json={"decision": "allow"})).status_code == 404
    for body in ({"decision": "maybe"}, {}, {"decision": "ALLOW"}):
        bad = await api.post(f"/api/v1/threads/{one['id']}/approvals/{approval['id']}", json=body)
        assert bad.status_code == 422
    assert (await api.get(f"/api/v1/threads/{one['id']}/approvals", params={"status": "bogus"})).status_code == 422
    assert [a["status"] for a in (await api.get(f"/api/v1/threads/{one['id']}/approvals")).json()] == ["pending"]  # untouched

    await api.post(f"/api/v1/threads/{one['id']}/approvals/{approval['id']}", json={"decision": "deny"})
    await wait_for_terminals(api, one["id"])
