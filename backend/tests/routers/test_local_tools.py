"""F03: local Claude with tools, every call waiting for a person.

A real child process plays the Claude CLI: it prints stream-json, asks for
approval with a `can_use_tool` control request, then blocks reading stdin until
the app answers, exactly as the real CLI does. Everything from the pipes to the
database and the HTTP routes is the real code."""

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
from app.services import local_approval_broker, local_claude_runner, task_execution_service
from app.services.local_approval_broker import broker
from app.services.local_run_hub import hub as run_hub
from tests.conftest import using_sqlite


def local_app(db_session) -> FastAPI:
    app = FastAPI()
    app.include_router(local_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    return app


def client_for(app, *, client=("127.0.0.1", 50000), base_url="http://localhost"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app, client=client), base_url=base_url
    )


@pytest.fixture(autouse=True)
def local_tools_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture(autouse=True)
def fresh_state():
    run_hub._runs.clear()
    broker._pending.clear()
    yield
    for approval in list(broker._pending.values()):
        if approval.timer is not None:
            approval.timer.cancel()
    broker._pending.clear()
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


TOOL_INPUT = {"file_path": "notes.txt", "content": "hello"}

# Plays the CLI. Writes what it received next to itself so the test can check
# the exact bytes the app sent back.
FAKE_CLI = r"""
import json, sys
out = {out!r}
def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()

first = sys.stdin.readline()
open(out + "-prompt.json", "w", encoding="utf-8").write(first)

emit({{"type": "assistant", "message": {{"id": "m1", "content": [
    {{"type": "tool_use", "id": "tu1", "name": "Write", "input": {tool_input!r}}}]}}}})
emit({{"type": "control_request", "request_id": "req-1", "request": {{
    "subtype": "can_use_tool", "tool_name": "Write", "input": {tool_input!r},
    "tool_use_id": "tu1"}}}})

reply = sys.stdin.readline()
open(out + "-reply.json", "w", encoding="utf-8").write(reply)
behavior = json.loads(reply)["response"]["response"]["behavior"]

emit({{"type": "user", "message": {{"content": [
    {{"type": "tool_result", "tool_use_id": "tu1", "is_error": behavior != "allow",
      "content": "File created" if behavior == "allow" else "denied"}}]}}}})
emit({{"type": "result", "is_error": False, "result": "finished: " + behavior,
      "permission_denials": [] if behavior == "allow" else [{{"tool_name": "Write"}}]}})

# The real CLI keeps reading until stdin closes; leaving early would hide a
# missing close.
sys.stdin.read()
open(out + "-eof", "w").write("closed")
"""


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    script = tmp_path / "fake_cli.py"
    out = str(tmp_path / "cli")
    script.write_text(FAKE_CLI.format(out=out, tool_input=TOOL_INPUT), encoding="utf-8")
    monkeypatch.setattr(
        local_claude_runner, "resolve_claude_executable", lambda: tmp_path / "claude"
    )
    built = {}

    def fake_build_args(executable, session_id, tools=False):
        built["tools"] = tools
        return [sys.executable, str(script)]

    monkeypatch.setattr(local_claude_runner, "build_args", fake_build_args)
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    return {"out": out, "built": built, "tmp": tmp_path}


async def start_run(db_session, user_id, monkeypatch) -> Tasks:
    task = Tasks(
        user_id=user_id, provider="claude-local", box_id="local", prompt_text="write a note",
        status="running", session_id="sid-1",
    )
    db_session.add(task)
    await db_session.commit()
    monkeypatch.setattr(task_execution_service, "AsyncSessionLocal", FakeSessionLocal(db_session))
    run_hub.start(task.id)
    return task


async def wait_for_approval(task_id, timeout=15.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        pending = broker.list_pending(task_id)
        if pending:
            return pending[0]
        await asyncio.sleep(0.05)
    raise AssertionError("the run never asked for approval")


async def collect_types(subscription) -> list[str]:
    from app.services import local_run_hub

    types = []
    async for frame in local_run_hub.stream_frames(subscription, heartbeat_seconds=5):
        if frame.startswith(":"):
            continue
        types.append(json.loads(frame.split("data: ")[1])["type"])
    return types


async def test_allowed_tool_call_runs_with_exactly_the_input_that_was_shown(
    db_session, user_id, monkeypatch, fake_cli
):
    task = await start_run(db_session, user_id, monkeypatch)
    subscription = run_hub.subscribe(task.id)
    listener = asyncio.create_task(collect_types(subscription))
    run = asyncio.create_task(task_execution_service._run_local_task(task.id))

    approval = await wait_for_approval(task.id)
    assert approval["tool"] == "Write"
    assert approval["input"] == TOOL_INPUT
    assert approval["summary"] == "notes.txt"

    async with client_for(local_app(db_session)) as client:
        listed = await client.get(f"/api/v1/local/tasks/{task.id}/approvals")
        assert [a["id"] for a in listed.json()] == [approval["id"]]
        answered = await client.post(
            f"/api/v1/local/tasks/{task.id}/approvals/{approval['id']}",
            json={"decision": "allow"},
        )
    assert answered.status_code == 200

    await asyncio.wait_for(run, 20)
    types = await asyncio.wait_for(listener, 5)
    await db_session.refresh(task)

    assert fake_cli["built"]["tools"] is True
    prompt = json.loads((fake_cli["tmp"] / "cli-prompt.json").read_text(encoding="utf-8"))
    assert prompt == {"type": "user", "message": {"role": "user", "content": "write a note"}}

    reply = json.loads((fake_cli["tmp"] / "cli-reply.json").read_text(encoding="utf-8"))
    assert reply == {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": "req-1",
            "response": {"behavior": "allow", "updatedInput": TOOL_INPUT},
        },
    }
    assert (fake_cli["tmp"] / "cli-eof").exists(), "stdin was never closed after the result"
    assert task.status == "succeeded" and task.result == "finished: allow"
    assert types[0] == "turn.started" and types[-1] == "turn.completed"
    for expected in ("tool.started", "approval.requested", "approval.resolved", "tool.completed"):
        assert expected in types
    assert broker.list_pending(task.id) == []


async def test_denied_tool_call_is_refused_and_the_run_still_finishes(
    db_session, user_id, monkeypatch, fake_cli
):
    task = await start_run(db_session, user_id, monkeypatch)
    run = asyncio.create_task(task_execution_service._run_local_task(task.id))
    approval = await wait_for_approval(task.id)

    async with client_for(local_app(db_session)) as client:
        answered = await client.post(
            f"/api/v1/local/tasks/{task.id}/approvals/{approval['id']}",
            json={"decision": "deny"},
        )
    assert answered.status_code == 200
    await asyncio.wait_for(run, 20)
    await db_session.refresh(task)

    reply = json.loads((fake_cli["tmp"] / "cli-reply.json").read_text(encoding="utf-8"))
    assert reply["response"]["response"] == {
        "behavior": "deny", "message": local_approval_broker.DENY_BY_USER,
    }
    # A refusal is the person's choice, not a failed run.
    assert task.status == "succeeded"
    assert task.result == "finished: deny"


async def test_an_unanswered_approval_expires_as_a_refusal(
    db_session, user_id, monkeypatch, fake_cli
):
    monkeypatch.setattr(settings, "LOCAL_APPROVAL_TIMEOUT_SECONDS", 0.4)
    task = await start_run(db_session, user_id, monkeypatch)
    run = asyncio.create_task(task_execution_service._run_local_task(task.id))
    await wait_for_approval(task.id)

    await asyncio.wait_for(run, 20)
    reply = json.loads((fake_cli["tmp"] / "cli-reply.json").read_text(encoding="utf-8"))
    assert reply["response"]["response"]["behavior"] == "deny"
    assert reply["response"]["response"]["message"] == local_approval_broker.DENY_EXPIRED
    assert broker.list_pending(task.id) == []


async def test_answering_twice_or_with_a_wrong_id_is_refused(
    db_session, user_id, monkeypatch, fake_cli
):
    task = await start_run(db_session, user_id, monkeypatch)
    run = asyncio.create_task(task_execution_service._run_local_task(task.id))
    approval = await wait_for_approval(task.id)
    url = f"/api/v1/local/tasks/{task.id}/approvals"

    async with client_for(local_app(db_session)) as client:
        wrong = await client.post(f"{url}/not-a-real-id", json={"decision": "allow"})
        garbage = await client.post(f"{url}/{approval['id']}", json={"decision": "maybe"})
        first = await client.post(f"{url}/{approval['id']}", json={"decision": "deny"})
        second = await client.post(f"{url}/{approval['id']}", json={"decision": "allow"})
    await asyncio.wait_for(run, 20)

    assert wrong.status_code == 404
    assert garbage.status_code == 422
    assert first.status_code == 200
    assert second.status_code == 404
    reply = json.loads((fake_cli["tmp"] / "cli-reply.json").read_text(encoding="utf-8"))
    assert reply["response"]["response"]["behavior"] == "deny", "the first answer must stand"


async def test_approval_routes_refuse_remote_clients_and_other_tasks(
    db_session, user_id, monkeypatch
):
    boat = Tasks(
        user_id=user_id, provider="claude", box_id="bx_abcdefgh",
        prompt_text="x", status="running", session_id="s",
    )
    local = Tasks(
        user_id=user_id, provider="claude-local", box_id="local",
        prompt_text="x", status="running", session_id="s2",
    )
    db_session.add_all([boat, local])
    await db_session.flush()

    async with client_for(local_app(db_session), client=("203.0.113.9", 4000)) as remote:
        listed = await remote.get(f"/api/v1/local/tasks/{local.id}/approvals")
        posted = await remote.post(
            f"/api/v1/local/tasks/{local.id}/approvals/abc", json={"decision": "allow"}
        )
    assert listed.status_code == 403 and posted.status_code == 403

    async with client_for(local_app(db_session)) as client:
        foreign_origin = await client.post(
            f"/api/v1/local/tasks/{local.id}/approvals/abc",
            json={"decision": "allow"},
            headers={"Origin": "https://evil.example"},
        )
        not_local = await client.get(f"/api/v1/local/tasks/{boat.id}/approvals")
        missing = await client.get("/api/v1/local/tasks/999999/approvals")
    assert foreign_origin.status_code == 403
    assert not_local.status_code == 404 and missing.status_code == 404


async def test_an_approval_for_another_run_cannot_be_answered_through_this_one(
    db_session, user_id
):
    answered = []
    approval = broker.request(
        1, "req", "Bash", {"command": "ls"}, lambda payload: answered.append(payload) or True
    )
    assert approval is not None
    with pytest.raises(local_approval_broker.ApprovalNotFound):
        broker.answer(2, approval.id, "allow")
    assert answered == []
    broker.cancel_run(1)


async def test_the_run_ending_cancels_open_questions(db_session, user_id, monkeypatch, fake_cli):
    # A CLI that asks and then dies without waiting for the answer.
    dying = fake_cli["tmp"] / "dying_cli.py"
    dying.write_text(
        "import json, sys\n"
        "sys.stdin.readline()\n"
        "sys.stdout.write(json.dumps({'type': 'control_request', 'request_id': 'r', "
        "'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash', "
        "'input': {'command': 'ls'}}}) + '\\n')\n"
        "sys.stdout.flush()\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        local_claude_runner, "build_args",
        lambda executable, session_id, tools=False: [sys.executable, str(dying)],
    )
    task = await start_run(db_session, user_id, monkeypatch)
    run = asyncio.create_task(task_execution_service._run_local_task(task.id))

    await asyncio.wait_for(run, 20)
    await db_session.refresh(task)
    assert broker.list_pending(task.id) == [], "a dead run must not leave a live question"
    assert task.status == "failed"


async def test_waiting_for_a_person_does_not_use_up_the_run_time_limit(
    db_session, user_id, monkeypatch, fake_cli
):
    monkeypatch.setattr(settings, "LOCAL_TASK_TIMEOUT_SECONDS", 2)
    task = await start_run(db_session, user_id, monkeypatch)
    run = asyncio.create_task(task_execution_service._run_local_task(task.id))
    approval = await wait_for_approval(task.id)

    await asyncio.sleep(3)  # longer than the whole limit, spent waiting
    async with client_for(local_app(db_session)) as client:
        await client.post(
            f"/api/v1/local/tasks/{task.id}/approvals/{approval['id']}",
            json={"decision": "allow"},
        )
    await asyncio.wait_for(run, 20)
    await db_session.refresh(task)
    assert task.status == "succeeded", task.error


def test_a_refused_tool_call_is_not_a_failed_run_with_tools_on_but_is_in_chat_only():
    from app.services.local_claude_stream import StreamNormalizer

    normalizer = StreamNormalizer(max_text_bytes=1000)
    normalizer.feed(json.dumps({
        "type": "result", "is_error": False, "result": "I could not do that",
        "permission_denials": [{"tool_name": "Write"}],
    }))
    output = local_claude_runner.RunOutput(stdout="", stderr="", exit_code=0)

    with_tools = task_execution_service._local_run_values(normalizer, output, tools=True)
    chat_only = task_execution_service._local_run_values(normalizer, output, tools=False)
    assert with_tools == {"status": "succeeded", "result": "I could not do that"}
    assert chat_only["status"] == "failed"


async def test_a_live_run_waiting_for_approval_is_not_swept_as_stale(
    db_session, user_id, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    monkeypatch.setattr(task_execution_service, "AsyncSessionLocal", FakeSessionLocal(db_session))
    live = Tasks(
        user_id=user_id, provider="claude-local", box_id="local",
        prompt_text="x", status="running", session_id="live",
    )
    dead = Tasks(
        user_id=user_id, provider="claude-local", box_id="local",
        prompt_text="y", status="running", session_id="dead",
    )
    db_session.add_all([live, dead])
    await db_session.commit()
    old = datetime.now(UTC) - timedelta(hours=1)
    await db_session.execute(update(Tasks).values(updated_at=old))
    await db_session.commit()
    run_hub.start(live.id)  # this process is still driving it

    await task_execution_service.sync_active_tasks()
    await db_session.refresh(live)
    await db_session.refresh(dead)
    assert live.status == "running"
    assert dead.status == "failed"


@pytest.mark.skipif(not using_sqlite(), reason="the lock is a SQLite behavior")
async def test_the_stale_run_sweep_survives_another_process_writing_meanwhile(monkeypatch):
    """Another process (the pairing command, a second tool) commits between the sweep's read
    and its write. A read-then-write transaction on SQLite then fails at once with "database
    is locked", whatever the busy timeout; the sweep must hold the write lock from the start."""
    import sqlite3
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select, update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.engine import create_app_async_engine

    engine = create_app_async_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_execution_service, "AsyncSessionLocal", sessions)
    async with sessions() as db:
        dead = Tasks(user_id="race-user", provider="claude-local", box_id="local", prompt_text="y",
                     status="running", session_id="race-dead")
        db.add(dead)
        await db.commit()
        task_id = dead.id
        await db.execute(update(Tasks).where(Tasks.id == task_id).values(updated_at=datetime.now(UTC) - timedelta(hours=1)))
        await db.commit()

    def another_process_writes(_task_id):
        connection = sqlite3.connect(settings.SQLITE_PATH, timeout=0.3)
        try:
            connection.execute("UPDATE task SET prompt_text = 'touched by another process' WHERE id = ?", (task_id,))
            connection.commit()
        except sqlite3.OperationalError:
            pass  # held off by the sweep's write lock: the safe outcome
        finally:
            connection.close()
        return False

    try:
        monkeypatch.setattr(run_hub, "is_live", another_process_writes)
        await task_execution_service.sync_active_tasks()  # must not raise "database is locked"
        async with sessions() as db:
            status = (await db.execute(select(Tasks.status).where(Tasks.id == task_id))).scalar_one()
        assert status == "failed"
    finally:
        async with sessions() as db:
            await db.execute(Tasks.__table__.delete().where(Tasks.id == task_id))
            await db.commit()
        await engine.dispose()
