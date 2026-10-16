"""Saved configuration exercised through the adapter and a real child process."""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.core import app_settings
from app.services import local_claude_runner
from app.services.thread_runtime import ThreadRuntime
from app.services.thread_hub import ThreadHub
from app.services import thread_store
from tests.services.thread_harness import ReadyClaude, FAKE_CLI, wait_until, finished
from tests.services.test_app_settings import configured, put  # noqa: F401


@pytest.fixture
async def execution(configured, monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"steps": [{"type": "chat"}]}))
    def args(executable, session_id, tools=False, resume=False):
        return [str(executable), str(FAKE_CLI), str(plan), "resume" if resume else "new", session_id, "1" if tools else "0"]
    monkeypatch.setattr(local_claude_runner, "build_args", args)
    await put(configured, "LOCAL_CLAUDE_CLI_PATH", sys.executable)
    await put(configured, "LOCAL_WORKSPACE_ROOT", str(tmp_path / "chosen"))
    await put(configured, "LOCAL_TOOLS_ENABLED", False)
    rt = ThreadRuntime(adapters={"claude": ReadyClaude()}, hub=ThreadHub(), _session_factory=configured, owner_id="settings-" + uuid4().hex, status_ttl=0)
    thread = await rt.create_thread()
    yield rt, thread, plan
    await rt.shutdown()
    async with configured() as db:
        await thread_store.delete_thread(db, thread.id)


async def update(sessions, key, value):
    return await put(sessions, key, value, app_settings.resolve(key, live=True).version)


async def turn(sessions, id):
    from app.db.models import Turn
    async with sessions() as db:
        return await db.get(Turn, id)


async def test_a_saved_change_affects_the_next_child_not_the_running_one(execution, configured, tmp_path):
    rt, thread, plan = execution
    release = tmp_path / "release"
    text = "x" * 6000
    plan.write_text(json.dumps({"steps": [{"type": "chat", "text": text, "wait_for": str(release)}, {"type": "chat", "text": text}]}))
    first = await rt.send_turn(thread.id, "first")
    await wait_until(lambda: (tmp_path / "log.jsonl").exists() and '"waiting"' in (tmp_path / "log.jsonl").read_text())
    await update(configured, "LOCAL_TOOLS_ENABLED", True)
    await update(configured, "LOCAL_MAX_OUTPUT_BYTES", 1024)
    await update(configured, "LOCAL_TASK_TIMEOUT_SECONDS", 60)
    release.write_text("go")
    await finished(configured, thread.id)
    assert (await turn(configured, first.id)).status == "succeeded"
    second = await rt.send_turn(thread.id, "second")
    await finished(configured, thread.id, 2)
    assert (await turn(configured, second.id)).error_code == "output_too_large"
    starts = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines() if json.loads(line)["event"] == "start"]
    assert [row["tools"] for row in starts] == [False, True]
    assert all(Path(row["cwd"]) == Path(thread.workspace_path) for row in starts)


async def test_saved_timeout_really_ends_the_child(execution, configured):
    rt, thread, plan = execution
    await put(configured, "LOCAL_TASK_TIMEOUT_SECONDS", 30)
    plan.write_text(json.dumps({"steps": [{"type": "hang"}]}))
    request = await rt.send_turn(thread.id, "wait")
    await wait_until(lambda: _done(configured, request.id), timeout=40)
    result = await turn(configured, request.id)
    assert result.error_code == "timeout"
    assert "30 seconds" in result.error


async def _done(sessions, id):
    return (await turn(sessions, id)).status != "running"


async def test_new_approvals_get_new_deadlines_without_changing_a_waiting_one(execution, configured):
    rt, thread, plan = execution
    await update(configured, "LOCAL_TOOLS_ENABLED", True)
    await put(configured, "LOCAL_APPROVAL_TIMEOUT_SECONDS", 60)
    plan.write_text(json.dumps({"steps": [{"type": "tool"}]}))
    first = await rt.send_turn(thread.id, "ask")
    async def pending():
        async with configured() as db:
            approvals = await thread_store.list_approvals(db, thread.id, status="pending")
            return approvals[0] if approvals else None
    approval = await wait_until(pending)
    deadline = rt._runs[first.id].gate._deadlines[approval.id]
    await update(configured, "LOCAL_APPROVAL_TIMEOUT_SECONDS", 30)
    assert rt._runs[first.id].gate._deadlines[approval.id] == deadline
    await rt.answer_approval(approval.id, "deny")
    await finished(configured, thread.id)
    second = await rt.send_turn(thread.id, "ask again")
    approval = await wait_until(pending)
    remaining = rt._runs[second.id].gate._deadlines[approval.id] - asyncio.get_running_loop().time()
    assert 25 < remaining <= 30
    await rt.answer_approval(approval.id, "deny")
    await finished(configured, thread.id, 2)
