"""The ACP agent engine, through a real runtime and a real child process playing
an ACP agent (tests/services/fake_acp_agent.py)."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import delete

from app.core.config import settings
from app.db.models import Agent, Thread, Turn
from app.db.models.user_credential import UserCredential
from app.services import agent_store, engine_config, local_claude_runner
from app.services import thread_store as store
from app.services.acp_adapter import AcpAdapter, InvalidConfig, history_preamble
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.thread_harness import (  # noqa: F401
    cleanup,
    events_of,
    finished,
    owner,
    pending_approval,
    reload,
    sessions,
    wait_until,
)

FAKE = Path(__file__).with_name("fake_acp_agent.py")


class FakeAcp:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.plan()

    def plan(self, *steps: dict, **more) -> None:
        (self.folder / "plan.json").write_text(json.dumps({"steps": list(steps) or [{"type": "chat", "text": "hello there"}], **more}))

    def log(self) -> list[dict]:
        path = self.folder / "log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def received(self, method: str) -> list[dict]:
        return [e["msg"] for e in self.log() if e.get("event") == "in" and e["msg"].get("method") == method]

    def answers(self) -> list:
        return [e["answer"] for e in self.log() if e.get("event") == "permission_answer"]

    def prompts(self) -> list[str]:
        return [e["text"] for e in self.log() if e.get("event") == "prompt_text"]


@pytest.fixture
def acp(monkeypatch, tmp_path):
    folder = tmp_path / "acp"
    folder.mkdir()
    monkeypatch.setenv("FAKE_ACP_DIR", str(folder))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: workspace)
    fake = FakeAcp(folder)
    fake.workspace = workspace
    fake.command = f"{sys.executable} {FAKE}"
    return fake


@pytest.fixture
async def art(sessions, owner, monkeypatch, acp):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    from app.services import thread_runtime

    monkeypatch.setattr(thread_runtime, "APPROVAL_POLL_SECONDS", 0.1)
    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"acp": AcpAdapter()}, status_ttl=0, _session_factory=sessions, owner_id=owner
    )
    async with sessions() as db:
        await engine_config.save(db, owner, "acp", {"command": acp.command, "label": None})
    yield runtime
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.execute(delete(UserCredential).where(UserCredential.user_id == owner))
        await db.commit()


async def acp_thread(art, sessions, owner, mode=None):
    agent_id = None
    if mode is not None:
        async with sessions() as db:
            agent = await agent_store.create_agent(db, owner_id=owner, name=f"acp-{mode}", provider="acp", approval_mode=mode)
        agent_id = agent.id
    return await art.create_thread(provider="acp", agent_id=agent_id)


def data_of(events, type_):
    return [e.data for e in events if e.type == type_]


async def test_a_chat_turn_streams_and_a_new_session_is_kept(art, sessions, owner, acp):
    thread = await acp_thread(art, sessions, owner)
    await art.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "assistant.message") == [{"text": "hello there "}]
    assert data_of(events, "turn.completed")[0]["result"] == "hello there "
    init = acp.received("initialize")[0]["params"]
    assert init["clientCapabilities"] == {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
    new = acp.received("session/new")[0]["params"]
    assert new["mcpServers"] == [] and Path(new["cwd"]).resolve() == acp.workspace.resolve()
    row = await reload(sessions, Thread, thread.id)
    assert row.provider_state["acp_session_id"].startswith("sess_")
    async with sessions() as db:
        turn = (await store.list_turns(db, thread.id))[-1]
    assert turn.status == "succeeded" and turn.usage == {"input_tokens": 5, "output_tokens": 3}


async def test_a_later_turn_loads_the_session_when_the_agent_can(art, sessions, owner, acp):
    acp.plan({"type": "chat", "text": "one"}, {"type": "chat", "text": "two"}, load=True)
    thread = await acp_thread(art, sessions, owner)
    await art.send_turn(thread.id, "first")
    await finished(sessions, thread.id)
    sid = (await reload(sessions, Thread, thread.id)).provider_state["acp_session_id"]
    await art.send_turn(thread.id, "second")
    await finished(sessions, thread.id, count=2)
    assert [m["params"]["sessionId"] for m in acp.received("session/load")] == [sid]
    assert len(acp.received("session/new")) == 1
    events = await events_of(sessions, thread.id)
    texts = [d["text"] for d in data_of(events, "assistant.message")]
    assert texts == ["one ", "two "]  # replayed history is not recorded


async def test_an_agent_that_cannot_load_gets_the_conversation_as_text(art, sessions, owner, acp):
    acp.plan({"type": "chat", "text": "one"}, {"type": "chat", "text": "two"})
    thread = await acp_thread(art, sessions, owner)
    await art.send_turn(thread.id, "first question")
    await finished(sessions, thread.id)
    await art.send_turn(thread.id, "second question")
    await finished(sessions, thread.id, count=2)
    assert acp.received("session/load") == [] and len(acp.received("session/new")) == 2
    last = acp.prompts()[-1]
    assert "User: first question" in last and "Assistant: one" in last and last.endswith("second question")


async def test_a_lost_session_breaks_the_conversation(art, sessions, owner, acp):
    acp.plan(load=True)
    thread = await acp_thread(art, sessions, owner)
    async with sessions() as db:
        await store.set_session_state(db, thread.id, "active")
        row = await db.get(Thread, thread.id)
        row.provider_state = {"acp_session_id": "gone"}
        await db.commit()
    await art.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    failed = data_of(await events_of(sessions, thread.id), "turn.failed")[0]
    assert failed["error_code"] == "session_resume_failed"
    assert (await reload(sessions, Thread, thread.id)).session_state == "broken"
    assert acp.received("session/prompt") == []


async def test_a_permission_request_waits_for_allow(art, sessions, owner, acp, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    acp.plan({"type": "permission", "kind": "execute", "command": "ls -la"})
    thread = await acp_thread(art, sessions, owner, mode="ask")
    await art.send_turn(thread.id, "list")
    approval = await pending_approval(sessions, thread.id)
    assert approval.tool == "Bash" and approval.input == {"command": "ls -la"} and approval.kind == "command"
    assert await art.answer_approval(approval.id, "allow")
    await finished(sessions, thread.id)
    # Only allow_once is ever picked, never allow_always.
    assert acp.answers()[0]["result"]["outcome"] == {"outcome": "selected", "optionId": "once"}
    events = await events_of(sessions, thread.id)
    assert data_of(events, "tool.started")[0]["name"] == "execute"
    assert data_of(events, "tool.completed")[0]["ok"] is True
    assert data_of(events, "turn.completed")[0]["result"].strip() == "Done."


async def test_a_denied_request_picks_reject_once(art, sessions, owner, acp, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    acp.plan({"type": "permission"})
    thread = await acp_thread(art, sessions, owner, mode="ask")
    await art.send_turn(thread.id, "list")
    approval = await pending_approval(sessions, thread.id)
    assert await art.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    assert acp.answers()[0]["result"]["outcome"] == {"outcome": "selected", "optionId": "no"}
    events = await events_of(sessions, thread.id)
    assert data_of(events, "tool.completed")[0]["ok"] is False


async def test_allow_is_not_granted_when_only_allow_always_is_offered(art, sessions, owner, acp, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    acp.plan({"type": "only_always"})
    thread = await acp_thread(art, sessions, owner, mode="ask")
    await art.send_turn(thread.id, "list")
    approval = await pending_approval(sessions, thread.id)
    assert await art.answer_approval(approval.id, "allow")
    await finished(sessions, thread.id)
    assert acp.answers()[0]["result"]["outcome"] == {"outcome": "cancelled"}


async def test_plan_mode_and_tools_off_refuse_without_a_card(art, sessions, owner, acp, monkeypatch):
    acp.plan({"type": "permission"})
    off = await acp_thread(art, sessions, owner)  # tools off
    await art.send_turn(off.id, "go")
    await finished(sessions, off.id)
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    plan = await acp_thread(art, sessions, owner, mode="plan")
    await art.send_turn(plan.id, "go")
    await finished(sessions, plan.id)
    assert [a["result"]["outcome"]["optionId"] for a in acp.answers()] == ["no", "no"]
    async with sessions() as db:
        assert await store.list_approvals(db, off.id) == [] and await store.list_approvals(db, plan.id) == []


async def test_accept_edits_allows_only_edits_inside_the_workspace(art, sessions, owner, acp, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    thread = await acp_thread(art, sessions, owner, mode="accept_edits")
    inside = str(Path(thread.workspace_path) / "a.txt")
    acp.plan({"type": "permission", "kind": "edit", "locations": [inside]})
    await art.send_turn(thread.id, "write")
    await finished(sessions, thread.id)
    assert acp.answers()[0]["result"]["outcome"]["optionId"] == "once"
    acp.plan({"type": "permission", "kind": "edit", "locations": [str(tmp_path / "elsewhere.txt")]})
    (acp.folder / "prompts").unlink()
    await art.send_turn(thread.id, "write again")
    approval = await pending_approval(sessions, thread.id)
    assert approval.kind == "file_write"
    assert await art.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id, count=2)


async def test_file_system_requests_are_refused(art, sessions, owner, acp):
    acp.plan({"type": "fs"})
    thread = await acp_thread(art, sessions, owner)
    await art.send_turn(thread.id, "read")
    await finished(sessions, thread.id)
    answer = next(e["answer"] for e in acp.log() if e.get("event") == "fs_answer")
    assert answer["error"]["code"] == -32601


async def test_stop_sends_cancel_and_keeps_the_partial_text(art, sessions, owner, acp):
    acp.plan({"type": "slow"})
    thread = await acp_thread(art, sessions, owner)
    await art.send_turn(thread.id, "long")
    await wait_until(lambda: acp.received("session/prompt"))
    await asyncio.sleep(0.5)
    await art.stop_turn(thread.id)
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "turn.cancelled")[0]["error_code"] == "stopped"
    assert {"text": "thinking out loud", "incomplete": True} in data_of(events, "assistant.message")
    assert acp.received("session/cancel")


async def test_prompt_error_and_refusal_fail_the_turn(art, sessions, owner, acp):
    acp.plan({"type": "error"}, {"type": "refusal"})
    thread = await acp_thread(art, sessions, owner)
    await art.send_turn(thread.id, "one")
    await finished(sessions, thread.id)
    await art.send_turn(thread.id, "two")
    await finished(sessions, thread.id, count=2)
    failed = data_of(await events_of(sessions, thread.id), "turn.failed")
    assert [f["error_code"] for f in failed] == ["acp_error", "acp_error"]
    assert "agent fell over" in failed[0]["error"] and "refusal" in failed[1]["error"]


async def test_a_crash_mid_turn_fails_instead_of_hanging(art, sessions, owner, acp, tmp_path):
    script = tmp_path / "crasher.py"
    script.write_text(
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    if m['method'] == 'initialize':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'agentCapabilities':{}}}), flush=True)\n"
        "    elif m['method'] == 'session/new':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'sessionId':'s1'}}), flush=True)\n"
        "    elif m['method'] == 'session/prompt':\n"
        "        sys.stderr.write('agent blew up\\n'); sys.stderr.flush(); sys.exit(3)\n"
    )
    async with sessions() as db:
        await engine_config.save(db, owner, "acp", {"command": f"{sys.executable} {script}", "label": None})
    thread = await acp_thread(art, sessions, owner)
    await art.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    failed = data_of(await events_of(sessions, thread.id), "turn.failed")[0]
    assert failed["error_code"] == "cli_error" and "blew up" in failed["error"]


async def test_a_missing_or_unset_command_is_refused_with_the_reason(art, sessions, owner):
    from app.services.thread_runtime import ProviderUnavailable

    async with sessions() as db:
        await engine_config.save(db, owner, "acp", {"command": "/nonexistent/agent --acp", "label": None})
    thread = await acp_thread(art, sessions, owner)
    with pytest.raises(ProviderUnavailable, match="not found"):
        await art.send_turn(thread.id, "hi")
    async with sessions() as db:
        await engine_config.remove(db, owner, "acp")
    with pytest.raises(ProviderUnavailable, match="Set the agent's command"):
        await art.send_turn(thread.id, "again")


async def test_probe_reads_the_handshake(acp):
    adapter = AcpAdapter()
    assert not (await adapter.probe()).ready
    adapter.config = {"command": acp.command}
    probe = await adapter.probe()
    assert probe.ready and probe.installed and probe.version == "fake-agent 1.2"
    acp.plan(hang_init=True)
    from app.services import acp_adapter

    acp_adapter.PROBE_INIT_TIMEOUT_SECONDS, saved = 0.5, acp_adapter.PROBE_INIT_TIMEOUT_SECONDS
    try:
        hung = await adapter.probe()
    finally:
        acp_adapter.PROBE_INIT_TIMEOUT_SECONDS = saved
    assert not hung.ready and "handshake" in hung.reason
    adapter.config = {"command": "/nonexistent/x"}
    assert "not found" in (await adapter.probe()).reason


def test_config_validation_and_history_text():
    adapter = AcpAdapter()
    assert adapter.validate_config({"command": " gemini --acp ", "label": " G "}, None) == {"command": "gemini --acp", "label": "G"}
    for bad in ({}, {"command": "  "}, {"command": "a\nb"}, {"command": "x 'unclosed"}, {"command": "a" * 1001}):
        with pytest.raises(InvalidConfig):
            adapter.validate_config(bad, None)
    assert adapter.public_config({"command": "x", "label": None, "extra": 1}) == {"command": "x", "label": None}
    items = [{"turn_id": 1, "role": "user", "text": "q"}, {"turn_id": 1, "role": "assistant", "text": "a"}, {"turn_id": 2, "role": "user", "text": "now"}]
    text = history_preamble(items, 2)
    assert "User: q" in text and "Assistant: a" in text and "now" not in text
    assert history_preamble(items[2:], 2) == ""
