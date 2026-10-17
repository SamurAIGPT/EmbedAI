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
    ("PATCH", "/api/v1/threads/1", {"title": "renamed"}),
    ("DELETE", "/api/v1/threads/1", None),
    ("POST", "/api/v1/threads/1/stop", None),
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
    assert bad.status_code == 422 and bad.json()["detail"]["error_code"] == "unknown_provider"
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


# --- stopping an answer ----------------------------------------------------------------------------------


async def test_stop_ends_a_running_answer_as_cancelled_and_the_conversation_goes_on(api, cli):
    thread = await create(api)
    tid = thread["id"]
    cli.script({"type": "hang"}, {"type": "chat", "text": "Back again"})
    sent = await api.post(f"/api/v1/threads/{tid}/turns", json={"prompt_text": "work"})
    await wait_until(lambda: cli.log("hanging"))

    stopped = await api.post(f"/api/v1/threads/{tid}/stop")
    assert stopped.status_code == 202
    assert stopped.json() == {"status": "stopping", "turn_id": sent.json()["id"]}
    await wait_for_terminals(api, tid)

    page = await events(api, tid)
    assert [e["type"] for e in page["events"]] == ["turn.started", "assistant.message", "turn.cancelled"]
    assert page["events"][1]["data"]["incomplete"] is True
    detail = (await api.get(f"/api/v1/threads/{tid}")).json()
    assert detail["active_turn_id"] is None
    assert detail["turns"][0]["status"] == "cancelled" and detail["turns"][0]["error_code"] == "stopped"
    assert detail["session_state"] == "active"

    again = await api.post(f"/api/v1/threads/{tid}/turns", json={"prompt_text": "carry on"})
    assert again.status_code == 202
    await wait_for_terminals(api, tid, 2)
    assert (await api.get(f"/api/v1/threads/{tid}")).json()["turns"][1]["result"] == "Back again"


async def test_stop_with_nothing_running_is_a_409_with_a_code(api):
    thread = await create(api)
    response = await api.post(f"/api/v1/threads/{thread['id']}/stop")
    assert response.status_code == 409
    assert code_of(response) == "no_running_turn"


async def test_stop_for_a_turn_owned_by_another_process_says_so(api, sessions):
    thread = await create(api)
    async with sessions() as db:
        await store.start_turn(db, thread["id"], "elsewhere", owner_instance="another-process")
    response = await api.post(f"/api/v1/threads/{thread['id']}/stop")
    assert response.status_code == 409
    assert code_of(response) == "turn_not_stoppable"


# --- managing conversations -------------------------------------------------------------------------------


async def test_rename_sets_the_title_and_trims_it(api):
    thread = await create(api, title="first title")
    response = await api.patch(f"/api/v1/threads/{thread['id']}", json={"title": "  A better name  "})
    assert response.status_code == 200
    assert response.json()["title"] == "A better name"
    assert (await api.get(f"/api/v1/threads/{thread['id']}")).json()["title"] == "A better name"


@pytest.mark.parametrize("body", [{}, {"title": "   "}, {"title": ""}, {"title": "x" * 201}, {"archived": "maybe"}])
async def test_a_bad_update_is_a_422_and_changes_nothing(api, body):
    thread = await create(api, title="keep me")
    assert (await api.patch(f"/api/v1/threads/{thread['id']}", json=body)).status_code == 422
    assert (await api.get(f"/api/v1/threads/{thread['id']}")).json()["title"] == "keep me"


async def test_archive_hides_a_conversation_and_unarchive_restores_it(api):
    thread = await create(api, title="to archive")
    other = await create(api, title="stays")
    archived = await api.patch(f"/api/v1/threads/{thread['id']}", json={"archived": True})
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None
    assert [t["title"] for t in (await api.get("/api/v1/threads")).json()] == ["stays"]
    listed = (await api.get("/api/v1/threads", params={"include_archived": "true"})).json()
    assert sorted(t["title"] for t in listed) == ["stays", "to archive"]
    # still readable by address
    assert (await api.get(f"/api/v1/threads/{thread['id']}")).status_code == 200
    restored = await api.patch(f"/api/v1/threads/{thread['id']}", json={"archived": False})
    assert restored.json()["archived_at"] is None
    assert len((await api.get("/api/v1/threads")).json()) == 2
    assert other["id"] != thread["id"]


async def test_archive_and_delete_are_refused_while_an_answer_runs(api, cli):
    thread = await create(api, title="busy")
    tid = thread["id"]
    cli.script({"type": "hang"})
    await api.post(f"/api/v1/threads/{tid}/turns", json={"prompt_text": "work"})
    await wait_until(lambda: cli.log("hanging"))

    archive = await api.patch(f"/api/v1/threads/{tid}", json={"archived": True, "title": "should not apply"})
    assert archive.status_code == 409 and code_of(archive) == "turn_running"
    delete = await api.delete(f"/api/v1/threads/{tid}")
    assert delete.status_code == 409 and code_of(delete) == "turn_running"
    detail = (await api.get(f"/api/v1/threads/{tid}")).json()
    assert detail["archived_at"] is None and detail["title"] == "busy"  # a refused update changed nothing

    await api.post(f"/api/v1/threads/{tid}/stop")
    await wait_for_terminals(api, tid)
    assert (await api.delete(f"/api/v1/threads/{tid}")).status_code == 204


async def test_delete_removes_the_conversation_for_good(api, cli):
    thread = await create(api, title="doomed")
    tid = thread["id"]
    cli.script({"type": "chat", "text": "hello"})
    await api.post(f"/api/v1/threads/{tid}/turns", json={"prompt_text": "hi"})
    await wait_for_terminals(api, tid)
    keep = await create(api, title="keep")

    gone = await api.delete(f"/api/v1/threads/{tid}")
    assert gone.status_code == 204 and gone.content == b""
    for path in ("", "/events", "/approvals"):
        assert (await api.get(f"/api/v1/threads/{tid}{path}")).status_code == 404
    assert (await api.delete(f"/api/v1/threads/{tid}")).status_code == 404  # already gone
    remaining = (await api.get("/api/v1/threads", params={"include_archived": "true"})).json()
    assert [t["id"] for t in remaining] == [keep["id"]]


async def test_delete_ends_the_streams_listening_to_that_conversation(rt, sessions, owner):
    from tests.services.test_thread_stream import Stream

    thread = await new_thread(sessions, owner)
    stream = Stream(thread.id, sessions, rt.hub)
    await wait_until(lambda: rt.hub.subscriber_count(thread.id) == 1)
    async with client_for(app_for(rt)) as client:
        assert (await client.delete(f"/api/v1/threads/{thread.id}")).status_code == 204
    await wait_until(lambda: stream.task.done())
    assert rt.hub.subscriber_count(thread.id) == 0


async def test_another_owners_conversation_cannot_be_renamed_stopped_or_deleted(api, sessions):
    from sqlalchemy import delete

    from app.db.models import Thread

    async with sessions() as db:
        theirs = await store.create_thread(
            db, owner_id="someone-else", provider="claude", provider_session_id="s-x", title="theirs"
        )
    try:
        assert (await api.patch(f"/api/v1/threads/{theirs.id}", json={"title": "mine now"})).status_code == 404
        assert (await api.post(f"/api/v1/threads/{theirs.id}/stop")).status_code == 404
        assert (await api.delete(f"/api/v1/threads/{theirs.id}")).status_code == 404
        async with sessions() as db:
            assert (await store.get_thread(db, theirs.id)).title == "theirs"
    finally:
        async with sessions() as db:
            await db.execute(delete(Thread).where(Thread.id == theirs.id))
            await db.commit()


async def test_delete_removes_only_this_apps_records_and_touches_no_files_or_other_conversations(
    api, cli, sessions, monkeypatch
):
    import os
    import pathlib
    import shutil

    from sqlalchemy import func, select

    from app.db.models import Approval, Event, Thread, Turn

    async def rows_of(thread_id):
        async with sessions() as db:
            return {
                model.__tablename__: (
                    await db.execute(select(func.count()).select_from(model).where(model.thread_id == thread_id))
                ).scalar_one()
                for model in (Event, Turn, Approval)
            }

    doomed = await create(api, title="doomed")
    keep = await create(api, title="keep")
    for thread in (doomed, keep):
        cli.script({"type": "chat", "text": "hello from " + thread["title"]}, {"type": "chat", "text": "still here"})
        sent = await api.post(f"/api/v1/threads/{thread['id']}/turns", json={"prompt_text": "hi"})
        assert sent.status_code == 202
        await wait_for_terminals(api, thread["id"])
    sessions_dir = cli.plan.parent / "sessions"
    session_files = sorted(p.name for p in sessions_dir.iterdir())
    assert len(session_files) == 2  # one stand-in CLI session per conversation
    keep_before = await rows_of(keep["id"])
    assert (await rows_of(doomed["id"]))["event"] > 0

    # Any attempt to remove a file or folder while deleting fails the test.
    touched = []

    def refuse(*args, **kwargs):
        touched.append(args)
        raise AssertionError("delete must not remove files or folders")

    with monkeypatch.context() as blocked:
        for target in (os, "remove"), (os, "unlink"), (os, "rmdir"), (shutil, "rmtree"), (pathlib.Path, "unlink"), (pathlib.Path, "rmdir"):
            blocked.setattr(target[0], target[1], refuse)
        deleted = await api.delete(f"/api/v1/threads/{doomed['id']}")
    assert deleted.status_code == 204 and touched == []

    # Every related row of the deleted conversation is gone ...
    assert await rows_of(doomed["id"]) == {"event": 0, "turn": 0, "approval": 0}
    async with sessions() as db:
        assert (await db.execute(select(func.count()).select_from(Thread).where(Thread.id == doomed["id"]))).scalar_one() == 0
    # ... the other conversation is exactly as it was ...
    assert await rows_of(keep["id"]) == keep_before
    assert (await api.get(f"/api/v1/threads/{keep['id']}")).json()["title"] == "keep"
    # ... and no CLI session was touched: both are still there, and the survivor still continues.
    assert sorted(p.name for p in sessions_dir.iterdir()) == session_files
    again = await api.post(f"/api/v1/threads/{keep['id']}/turns", json={"prompt_text": "again"})
    assert again.status_code == 202
    await wait_for_terminals(api, keep["id"], 2)
    second = (await api.get(f"/api/v1/threads/{keep['id']}")).json()["turns"][1]
    assert second["status"] == "succeeded", (second["error_code"], second["error"])
