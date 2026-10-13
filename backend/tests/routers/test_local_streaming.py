"""F02: what a local run records and announces while and after it streams.

The fake CLI hands the same JSON lines the real one prints to the handler the
runner would use, so everything from parsing to the database and the events
is the real code."""

import asyncio
import json
import sys

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.db.models.task import Tasks
from app.db.session import get_db
from app.routers.local import router as local_router
from app.routers.task import router as task_router
from app.services import local_claude_runner, local_run_hub, task_execution_service
from app.services.local_run_hub import RunEvent
from app.services.local_run_hub import hub as run_hub


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


@pytest.fixture(autouse=True)
def fresh_hub():
    run_hub._runs.clear()
    yield
    for run in run_hub._runs.values():
        if run.cleanup is not None:
            run.cleanup.cancel()
    run_hub._runs.clear()


class FakeSessionLocal:
    def __init__(self, db):
        self.db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *exc):
        return False


def message_start(message="m1"):
    return json.dumps({
        "type": "stream_event", "parent_tool_use_id": None,
        "event": {"type": "message_start", "message": {"id": message}},
    })


def delta(text):
    return json.dumps({
        "type": "stream_event", "parent_tool_use_id": None,
        "event": {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": text}},
    })


def snapshot(text, message="m1"):
    return json.dumps({
        "type": "assistant", "parent_tool_use_id": None,
        "message": {"id": message, "content": [{"type": "text", "text": text}]},
    })


def result(text="", *, is_error=False, denials=None):
    return json.dumps({
        "type": "result", "subtype": "error_during_execution" if is_error else "success",
        "is_error": is_error, "result": text, "permission_denials": denials or [],
    })


async def run_local(
    db_session, user_id, monkeypatch, *, lines=(), stderr="", exit_code=0,
    timed_out=False, error=None, status="running", watch=False,
):
    """Runs _run_local_task against a fake CLI that prints `lines`."""
    local = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="say hi",
        status=status, session_id="sid-1",
    )
    db_session.add(local)
    await db_session.commit()
    monkeypatch.setattr(task_execution_service, "AsyncSessionLocal", FakeSessionLocal(db_session))
    run_hub.start(local.id)
    subscription = run_hub.subscribe(local.id) if watch else None
    stopped_by_handler: list[bool] = []

    async def fake_run(prompt, session_id, on_stdout_line=None):
        assert prompt == "say hi" and session_id == "sid-1"
        if error:
            raise error
        for line in lines:
            if on_stdout_line(line.encode("utf-8")):
                stopped_by_handler.append(True)
                break
        await asyncio.sleep(0)  # the real runner delivers lines in order before returning
        return local_claude_runner.RunOutput(
            stdout="", stderr=stderr, exit_code=exit_code, timed_out=timed_out,
            output_truncated=bool(stopped_by_handler),
        )

    monkeypatch.setattr(local_claude_runner, "run_prompt", fake_run)
    await task_execution_service._run_local_task(local.id)
    await db_session.refresh(local)
    return local, subscription


def drained(subscription) -> list[RunEvent]:
    events = list(subscription.initial)
    while not subscription.queue.empty():
        item = subscription.queue.get_nowait()
        if isinstance(item, RunEvent):
            events.append(item)
    return events


def streamed_text(events) -> str:
    return "".join(e.data["text"] for e in events if e.type == "text.delta")


def parse_sse(body: str) -> list[dict]:
    frames = []
    for block in body.split("\n\n"):
        data = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            frames.append(json.loads("".join(data)))
    return frames


# --- what is recorded and announced -----------------------------------------


async def test_successful_run_is_recorded(db_session, user_id, monkeypatch):
    task, _ = await run_local(
        db_session, user_id, monkeypatch,
        lines=[message_start(), delta("hi there"), result("hi there")],
    )
    assert task.status == "succeeded" and task.result == "hi there"


async def test_text_arrives_in_order_and_the_outcome_follows_the_database(
    db_session, user_id, monkeypatch
):
    task, sub = await run_local(
        db_session, user_id, monkeypatch, watch=True,
        lines=[message_start(), delta("Hel"), delta("lo "), delta("world"), result("Hello world")],
    )
    events = drained(sub)
    assert [e.type for e in events] == [
        "turn.started", "text.delta", "text.delta", "text.delta", "turn.completed",
    ]
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert streamed_text(events) == "Hello world"
    assert all(e.data["mode"] == "append" for e in events if e.type == "text.delta")
    assert events[-1].data == {"status": "succeeded", "result": "Hello world"}
    assert task.status == "succeeded"


async def test_full_snapshots_do_not_duplicate_streamed_text(db_session, user_id, monkeypatch):
    task, sub = await run_local(
        db_session, user_id, monkeypatch, watch=True,
        lines=[message_start(), delta("one "), delta("two"), snapshot("one two"), result("one two")],
    )
    assert streamed_text(drained(sub)) == "one two"
    assert task.result == "one two"


async def test_snapshot_only_output_is_used_once(db_session, user_id, monkeypatch):
    task, sub = await run_local(
        db_session, user_id, monkeypatch, watch=True,
        lines=[snapshot("whole answer", "m9"), snapshot("whole answer", "m9"), result("whole answer")],
    )
    assert streamed_text(drained(sub)) == "whole answer"
    assert task.result == "whole answer"


async def test_empty_output_is_a_successful_empty_result(db_session, user_id, monkeypatch):
    task, sub = await run_local(db_session, user_id, monkeypatch, watch=True, lines=[result("")])
    assert task.status == "succeeded" and task.result == ""
    assert drained(sub)[-1].type == "turn.completed"


async def test_missing_final_text_keeps_the_streamed_answer(db_session, user_id, monkeypatch):
    task, _ = await run_local(
        db_session, user_id, monkeypatch, lines=[message_start(), delta("kept"), result("")]
    )
    assert task.status == "succeeded" and task.result == "kept"


async def test_final_result_replaces_the_streamed_text(db_session, user_id, monkeypatch):
    task, _ = await run_local(
        db_session, user_id, monkeypatch, lines=[message_start(), delta("draft"), result("final")]
    )
    assert task.result == "final"


async def test_failure_before_any_response_shows_the_error(db_session, user_id, monkeypatch):
    task, sub = await run_local(
        db_session, user_id, monkeypatch, watch=True, exit_code=1,
        lines=[result("No conversation found with session ID", is_error=True)],
    )
    assert task.status == "failed"
    assert task.error == "No conversation found with session ID"
    assert not task.result
    last = drained(sub)[-1]
    assert last.type == "turn.failed" and last.data["error"] == task.error


async def test_failure_after_partial_text_keeps_the_text(db_session, user_id, monkeypatch):
    task, sub = await run_local(
        db_session, user_id, monkeypatch, watch=True, exit_code=1, stderr="CLI crashed",
        lines=[message_start(), delta("half an ans")],
    )
    assert task.status == "failed"
    assert task.result == "half an ans"
    assert task.error == "CLI crashed"
    last = drained(sub)[-1]
    assert last.type == "turn.failed"
    assert last.data["result"] == "half an ans" and last.data["error"] == "CLI crashed"


async def test_ending_without_a_result_is_not_a_success(db_session, user_id, monkeypatch):
    task, _ = await run_local(
        db_session, user_id, monkeypatch, lines=[message_start(), delta("text")], exit_code=0
    )
    assert task.status == "failed"
    assert task.error == "Claude ended without a result"
    assert task.result == "text"


async def test_claude_error_result_is_a_failure(db_session, user_id, monkeypatch):
    task, _ = await run_local(
        db_session, user_id, monkeypatch, lines=[result("model refused", is_error=True)]
    )
    assert task.status == "failed" and task.error == "model refused"


async def test_a_refused_tool_use_is_a_failure_even_if_the_result_says_success(
    db_session, user_id, monkeypatch
):
    task, _ = await run_local(
        db_session, user_id, monkeypatch, lines=[result("done", denials=[{"tool_name": "Bash"}])]
    )
    assert task.status == "failed" and "tool" in task.error


async def test_timeout_is_a_failure_and_keeps_partial_text(db_session, user_id, monkeypatch):
    task, sub = await run_local(
        db_session, user_id, monkeypatch, watch=True, timed_out=True, exit_code=1,
        lines=[message_start(), delta("so far")],
    )
    assert task.status == "failed" and "Timed out" in task.error
    assert task.result == "so far"
    assert drained(sub)[-1].type == "turn.failed"


async def test_oversized_output_is_a_failure_and_keeps_partial_text(
    db_session, user_id, monkeypatch
):
    monkeypatch.setattr(settings, "LOCAL_MAX_OUTPUT_BYTES", 10)
    task, _ = await run_local(
        db_session, user_id, monkeypatch,
        lines=[message_start(), delta("12345"), delta("67890"), delta("overflow"), result("x")],
    )
    assert task.status == "failed" and "more output" in task.error
    assert task.result == "1234567890"


async def test_non_json_output_is_a_failure_with_stderr(db_session, user_id, monkeypatch):
    task, _ = await run_local(
        db_session, user_id, monkeypatch, lines=["garbage"], stderr="No conversation found",
        exit_code=1,
    )
    assert task.status == "failed" and task.error == "No conversation found"


async def test_a_nonzero_exit_without_stderr_still_explains_itself(
    db_session, user_id, monkeypatch
):
    task, _ = await run_local(db_session, user_id, monkeypatch, exit_code=2)
    assert task.status == "failed" and task.error == "Claude exited with code 2"


async def test_missing_cli_during_the_run_is_a_failure(db_session, user_id, monkeypatch):
    task, sub = await run_local(
        db_session, user_id, monkeypatch, watch=True,
        error=local_claude_runner.ClaudeCliUnavailable("Claude CLI not found."),
    )
    assert task.status == "failed" and task.error == "Claude CLI not found."
    assert drained(sub)[-1].type == "turn.failed"


async def test_crash_is_recorded_without_leaking_details(db_session, user_id, monkeypatch):
    task, _ = await run_local(
        db_session, user_id, monkeypatch, error=RuntimeError("secret path C:/x")
    )
    assert task.status == "failed"
    assert task.error == "Local run failed unexpectedly"


async def test_informational_events_do_not_disturb_a_run(db_session, user_id, monkeypatch):
    noise = [
        json.dumps({"type": "system", "subtype": "init", "tools": []}),
        json.dumps({"type": "system", "subtype": "status", "status": "requesting"}),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}),
        json.dumps({"type": "system", "subtype": "thinking_tokens", "tokens": 3}),
        json.dumps({"type": "something_new", "x": 1}),
    ]
    task, sub = await run_local(
        db_session, user_id, monkeypatch, watch=True,
        lines=[*noise, message_start(), delta("ok"), noise[1], delta("!"), result("ok!")],
    )
    assert streamed_text(drained(sub)) == "ok!"
    assert task.status == "succeeded" and task.result == "ok!"


async def test_terminal_event_reports_the_database_not_the_runner(
    db_session, user_id, monkeypatch
):
    """A run the stale sweep already failed must never be announced as done."""
    task, sub = await run_local(
        db_session, user_id, monkeypatch, watch=True, status="failed",
        lines=[message_start(), delta("late answer"), result("late answer")],
    )
    assert task.status == "failed"
    last = drained(sub)[-1]
    assert last.type == "turn.failed" and last.data["status"] == "failed"


async def test_a_listener_after_completion_still_gets_the_outcome(
    db_session, user_id, monkeypatch
):
    task, _ = await run_local(
        db_session, user_id, monkeypatch, lines=[message_start(), delta("done"), result("done")]
    )
    events = drained(run_hub.subscribe(task.id))
    assert [e.type for e in events] == ["text.delta", "turn.completed"]
    assert events[0].data == {"text": "done", "mode": "replace"}
    assert events[1].data == {"status": "succeeded", "result": "done"}


async def test_the_state_is_saved_before_the_terminal_event_is_announced(
    db_session, user_id, monkeypatch
):
    """When a listener sees the outcome, the task row already says the same."""
    seen = {}
    real_publish = run_hub.publish

    def spy(run_id, event_type, data=None):
        if event_type in ("turn.completed", "turn.failed"):
            seen["row"] = (local_ref[0].status, local_ref[0].result)
        return real_publish(run_id, event_type, data)

    local_ref: list = []
    original_reload = task_execution_service._reload

    async def capture(db, task_id):
        task = await original_reload(db, task_id)
        local_ref[:] = [task]
        return task

    monkeypatch.setattr(task_execution_service, "_reload", capture)
    monkeypatch.setattr(run_hub, "publish", spy)
    await run_local(
        db_session, user_id, monkeypatch, lines=[message_start(), delta("a"), result("a")]
    )
    assert seen["row"] == ("succeeded", "a")


async def test_a_disconnect_never_stops_the_run(db_session, user_id, monkeypatch):
    local = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="say hi",
        status="running", session_id="sid-1",
    )
    db_session.add(local)
    await db_session.commit()
    monkeypatch.setattr(task_execution_service, "AsyncSessionLocal", FakeSessionLocal(db_session))
    release = asyncio.Event()
    run_hub.start(local.id)
    sub = run_hub.subscribe(local.id)

    async def slow_run(prompt, session_id, on_stdout_line=None):
        on_stdout_line(delta("first").encode())
        await asyncio.sleep(0)
        await release.wait()
        on_stdout_line(result("first and more").encode())
        await asyncio.sleep(0)
        return local_claude_runner.RunOutput(stdout="", stderr="", exit_code=0)

    monkeypatch.setattr(local_claude_runner, "run_prompt", slow_run)
    run = asyncio.create_task(task_execution_service._run_local_task(local.id))
    await asyncio.sleep(0.05)

    run_hub.unsubscribe(sub)  # the browser went away
    assert run_hub.subscriber_count(local.id) == 0
    release.set()
    await run
    await db_session.refresh(local)
    assert local.status == "succeeded" and local.result == "first and more"


# --- the events route --------------------------------------------------------


@pytest.fixture
def ready_cli(monkeypatch):
    async def status():
        return local_claude_runner.CliStatus(installed=True, version="9.9.9", signed_in=True)

    monkeypatch.setattr(local_claude_runner, "get_status", status)


async def test_events_route_serves_a_finished_run_from_the_database(
    db_session, user_id, ready_cli
):
    task = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="x",
        status="succeeded", session_id="s", result="stored answer",
    )
    db_session.add(task)
    await db_session.flush()
    async with client_for(local_app(db_session)) as client:
        response = await client.get(f"/api/v1/local/tasks/{task.id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "no-cache" in response.headers["cache-control"]
    frames = parse_sse(response.text)
    assert [f["type"] for f in frames] == ["turn.completed"]
    assert frames[0]["data"] == {"status": "succeeded", "result": "stored answer"}
    assert frames[0]["run_id"] == task.id


async def test_events_route_reports_a_failed_run_from_the_database(
    db_session, user_id, ready_cli
):
    task = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="x",
        status="failed", session_id="s", result="partial", error="boom",
    )
    db_session.add(task)
    await db_session.flush()
    async with client_for(local_app(db_session)) as client:
        response = await client.get(f"/api/v1/local/tasks/{task.id}/events")
    frames = parse_sse(response.text)
    assert frames[0]["type"] == "turn.failed"
    assert frames[0]["data"] == {"status": "failed", "error": "boom", "result": "partial"}


async def test_events_route_reports_a_run_this_process_is_not_tracking(
    db_session, user_id, ready_cli
):
    task = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="x",
        status="running", session_id="s",
    )
    db_session.add(task)
    await db_session.flush()
    async with client_for(local_app(db_session)) as client:
        response = await client.get(f"/api/v1/local/tasks/{task.id}/events")
    frames = parse_sse(response.text)
    assert [f["type"] for f in frames] == ["runtime.status"]
    assert frames[0]["data"] == {"state": "not_live"}


async def test_events_route_streams_a_run_that_just_finished_in_memory(
    db_session, user_id, ready_cli
):
    task = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="x",
        status="succeeded", session_id="s", result="abc",
    )
    db_session.add(task)
    await db_session.flush()
    run_hub.start(task.id)
    run_hub.publish(task.id, "turn.started")
    run_hub.publish(task.id, "text.delta", {"text": "abc", "mode": "append"})
    run_hub.publish(task.id, "turn.completed", {"status": "succeeded", "result": "abc"})
    async with client_for(local_app(db_session)) as client:
        response = await client.get(f"/api/v1/local/tasks/{task.id}/events")
    frames = parse_sse(response.text)
    assert [f["type"] for f in frames] == ["text.delta", "turn.completed"]
    assert frames[0]["data"] == {"text": "abc", "mode": "replace"}


async def test_events_route_refuses_what_the_other_local_routes_refuse(
    db_session, user_id, ready_cli, monkeypatch
):
    task = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="x",
        status="succeeded", session_id="s", result="private",
    )
    boat = Tasks(
        user_id=user_id, provider="claude", box_id="bx_abcdefgh", prompt_text="x",
        status="succeeded", session_id="s2",
    )
    db_session.add_all([task, boat])
    await db_session.flush()
    url = f"/api/v1/local/tasks/{task.id}/events"

    async with client_for(local_app(db_session), client=("203.0.113.9", 4000)) as client:
        assert (await client.get(url)).status_code == 403
    async with client_for(local_app(db_session), base_url="https://abc.ngrok.app") as client:
        assert (await client.get(url)).status_code == 403
    async with client_for(local_app(db_session)) as client:
        foreign = await client.get(url, headers={"Origin": "https://evil.example"})
        assert foreign.status_code == 403
        assert (await client.get("/api/v1/local/tasks/999999999/events")).status_code == 404
        # A Boat task is not a local run: the events route must not serve it.
        assert (await client.get(f"/api/v1/local/tasks/{boat.id}/events")).status_code == 404
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", False)
    async with client_for(local_app(db_session)) as client:
        assert (await client.get(url)).status_code == 403


async def test_legacy_task_route_still_serves_boat_tasks(db_session, user_id, ready_cli):
    boat = Tasks(
        user_id=user_id, provider="claude", box_id="bx_abcdefgh", prompt_text="x",
        status="succeeded", session_id="s", result="boat answer",
    )
    db_session.add(boat)
    await db_session.flush()
    async with client_for(local_app(db_session)) as client:
        response = await client.get(f"/api/v1/tasks/{boat.id}")
    assert response.status_code == 200 and response.json()["result"] == "boat answer"


async def test_the_plain_task_route_still_reports_the_final_result(
    db_session, user_id, ready_cli, monkeypatch
):
    local, _ = await run_local(
        db_session, user_id, monkeypatch, lines=[message_start(), delta("hey"), result("hey")]
    )
    async with client_for(local_app(db_session)) as client:
        body = (await client.get(f"/api/v1/local/tasks/{local.id}")).json()
    assert body["status"] == "succeeded" and body["result"] == "hey"


async def test_a_real_process_streams_through_to_the_database_and_listeners(
    db_session, user_id, monkeypatch, tmp_path
):
    """No fake runner: a child process prints stream-json, slowly, and the
    text must reach a listener before the process has finished."""
    script = tmp_path / "fake_claude.py"
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        message_start(),
        delta("Hel"),
        "__pause__",
        delta("lo"),
        snapshot("Hello"),
        json.dumps({"type": "rate_limit_event", "rate_limit_info": {}}),
        result("Hello"),
    ]
    script.write_text(
        "import sys, time\n"
        "sys.stdin.read()\n"
        f"for line in {lines!r}:\n"
        "    if line == '__pause__':\n"
        "        time.sleep(1.5)\n"
        "        continue\n"
        "    sys.stdout.write(line + '\\r\\n')\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        local_claude_runner, "resolve_claude_executable", lambda: tmp_path / "claude"
    )
    monkeypatch.setattr(
        local_claude_runner, "build_args",
        lambda executable, session_id: [sys.executable, str(script)],
    )
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)

    local = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="say hi",
        status="running", session_id="sid-1",
    )
    db_session.add(local)
    await db_session.commit()
    monkeypatch.setattr(task_execution_service, "AsyncSessionLocal", FakeSessionLocal(db_session))
    run_hub.start(local.id)
    subscription = run_hub.subscribe(local.id)

    run = asyncio.create_task(task_execution_service._run_local_task(local.id))
    arrivals = []
    types = []
    async for frame in local_run_hub.stream_frames(subscription, heartbeat_seconds=5):
        if frame.startswith(":"):
            continue
        event = json.loads(frame.split("data: ")[1])
        types.append(event["type"])
        arrivals.append((event["type"], asyncio.get_running_loop().time()))
    ended = asyncio.get_running_loop().time()
    await run
    await db_session.refresh(local)

    assert types == ["turn.started", "text.delta", "text.delta", "turn.completed"]
    first_text = next(t for kind, t in arrivals if kind == "text.delta")
    assert ended - first_text > 1.0, "text was held back until the process finished"
    assert local.status == "succeeded" and local.result == "Hello"
