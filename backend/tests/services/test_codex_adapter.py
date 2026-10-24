"""The Codex engine, through a real runtime and a real child process playing
`codex` (tests/services/fake_codex_cli.py)."""

import json
import stat
import sys
from pathlib import Path

import pytest
from sqlalchemy import delete

from app.core.config import settings
from app.db.models import Agent, Thread, Turn
from app.services import agent_store, codex_adapter, local_claude_runner
from app.services import thread_store as store
from app.services.codex_adapter import CodexAdapter, codex_policy
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
    types_of,
    wait_until,
)

FAKE = Path(__file__).with_name("fake_codex_cli.py")


class FakeCodex:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.plan()

    def plan(self, *steps: dict, **more) -> None:
        (self.folder / "plan.json").write_text(json.dumps({"steps": list(steps) or [{"type": "chat", "text": "hello there"}], **more}))

    def log(self) -> list[dict]:
        path = self.folder / "log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def received(self, method: str) -> list[dict]:
        return [e["msg"] for e in self.log() if e.get("event") == "in" and e["msg"].get("method") == method]

    def answers(self) -> list:
        return [e["answer"] for e in self.log() if e.get("event") == "approval_answer"]


@pytest.fixture
def codex(monkeypatch, tmp_path):
    folder = tmp_path / "codex"
    folder.mkdir()
    exe = tmp_path / "codex-bin"
    exe.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_CODEX_DIR", str(folder))
    monkeypatch.setattr(codex_adapter, "resolve_codex_executable", lambda: exe)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: workspace)
    fake = FakeCodex(folder)
    fake.workspace = workspace
    return fake


@pytest.fixture
async def crt(sessions, owner, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    from app.services import thread_runtime

    monkeypatch.setattr(thread_runtime, "APPROVAL_POLL_SECONDS", 0.1)
    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"codex": CodexAdapter()}, status_ttl=0, _session_factory=sessions, owner_id=owner
    )
    yield runtime
    await runtime.shutdown()
    async with sessions() as db:
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


async def codex_thread(crt, sessions, owner, mode=None):
    agent_id = None
    if mode is not None:
        async with sessions() as db:
            agent = await agent_store.create_agent(db, owner_id=owner, name=f"cx-{mode}", provider="codex", approval_mode=mode)
        agent_id = agent.id
    return await crt.create_thread(provider="codex", agent_id=agent_id)


def data_of(events, type_):
    return [e.data for e in events if e.type == type_]


def test_policy_mapping():
    assert codex_policy("ask", True) == codex_adapter.CodexPolicy("untrusted", "workspace-write", "workspaceWrite")
    assert codex_policy("accept_edits", True).approval == "untrusted"
    assert codex_policy("plan", True) == codex_adapter.CodexPolicy("never", "read-only", "readOnly")
    assert codex_policy("ask", False).sandbox == "read-only"


async def test_a_chat_turn_then_a_resumed_one(crt, sessions, owner, codex):
    thread = await codex_thread(crt, sessions, owner)
    assert thread.session_state == "new"
    await crt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "assistant.message") == [{"text": "hello there"}]
    assert data_of(events, "turn.completed")[0]["result"] == "hello there"
    row = await reload(sessions, Thread, thread.id)
    assert row.session_state == "active"
    codex_id = row.provider_state["codex_thread_id"]
    started = codex.received("thread/start")[0]["params"]
    # Tools off: read-only, nothing asked.
    assert (started["approvalPolicy"], started["sandbox"]) == ("never", "read-only")
    assert started["cwd"] == str(codex.workspace.resolve()) or started["cwd"] == str(codex.workspace)
    turn = codex.received("turn/start")[0]["params"]
    assert turn["sandboxPolicy"] == {"type": "readOnly"}
    assert turn["input"] == [{"type": "text", "text": "hi"}]

    codex.plan({"type": "chat", "text": "again"})
    await crt.send_turn(thread.id, "more")
    await finished(sessions, thread.id, count=2)
    resumed = codex.received("thread/resume")
    assert [r["params"]["threadId"] for r in resumed] == [codex_id]
    turn = await reload(sessions, Turn, (await crt_turns(sessions, thread.id))[-1])
    assert turn.status == "succeeded" and turn.usage == {"input_tokens": 11, "output_tokens": 7, "cached_input_tokens": 3}


async def crt_turns(sessions, thread_id):
    async with sessions() as db:
        return [t.id for t in await store.list_turns(db, thread_id)]


async def test_a_command_waits_for_allow(crt, sessions, owner, codex, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    codex.plan({"type": "command", "command": "echo hi > out.txt"})
    thread = await codex_thread(crt, sessions, owner, mode="ask")
    await crt.send_turn(thread.id, "make a file")
    approval = await pending_approval(sessions, thread.id)
    assert approval.tool == "Bash"
    assert approval.input["command"] == "/bin/zsh -lc 'echo hi > out.txt'"
    assert approval.summary == "echo hi > out.txt"
    assert approval.kind == "command"
    started = codex.received("thread/start")[0]["params"]
    assert (started["approvalPolicy"], started["sandbox"]) == ("untrusted", "workspace-write")
    assert await crt.answer_approval(approval.id, "allow")
    await finished(sessions, thread.id)
    assert codex.answers()[0]["result"] == {"decision": "accept"}
    events = await events_of(sessions, thread.id)
    tool = data_of(events, "tool.started")[0]
    assert tool["name"] == "Bash" and tool["summary"] == "echo hi > out.txt"
    assert data_of(events, "tool.completed")[0]["ok"] is True
    assert (Path(thread.workspace_path) / "out.txt").read_text() == "hi\n"
    assert data_of(events, "turn.completed")[0]["result"] == "done"


async def test_a_denied_command_is_declined(crt, sessions, owner, codex, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    codex.plan({"type": "command"})
    thread = await codex_thread(crt, sessions, owner, mode="ask")
    await crt.send_turn(thread.id, "make a file")
    approval = await pending_approval(sessions, thread.id)
    assert await crt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    assert codex.answers()[0]["result"] == {"decision": "decline"}
    events = await events_of(sessions, thread.id)
    assert data_of(events, "tool.completed")[0]["ok"] is False
    assert not (Path(thread.workspace_path) / "out.txt").exists()


async def test_accept_edits_lets_a_workspace_edit_through_without_a_card(crt, sessions, owner, codex, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    codex.plan({"type": "file"})  # a file in the folder the turn runs in
    thread = await codex_thread(crt, sessions, owner, mode="accept_edits")
    await crt.send_turn(thread.id, "write notes")
    await finished(sessions, thread.id)
    assert codex.answers()[0]["result"] == {"decision": "accept"}
    async with sessions() as db:
        assert await store.list_approvals(db, thread.id) == []


async def test_accept_edits_still_asks_about_an_edit_outside_the_workspace(crt, sessions, owner, codex, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    codex.plan({"type": "file", "path": str(tmp_path / "elsewhere.txt")})
    thread = await codex_thread(crt, sessions, owner, mode="accept_edits")
    await crt.send_turn(thread.id, "write notes")
    approval = await pending_approval(sessions, thread.id)
    assert approval.tool == "apply_patch" and approval.kind == "file_write"
    assert approval.input["changes"][0]["path"] == str(tmp_path / "elsewhere.txt")
    assert await crt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    assert codex.answers()[0]["result"] == {"decision": "decline"}


async def test_plan_mode_is_read_only_and_declines_without_a_card(crt, sessions, owner, codex, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    codex.plan({"type": "command"})
    thread = await codex_thread(crt, sessions, owner, mode="plan")
    await crt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    started = codex.received("thread/start")[0]["params"]
    assert (started["approvalPolicy"], started["sandbox"]) == ("never", "read-only")
    assert codex.answers()[0]["result"] == {"decision": "decline"}
    async with sessions() as db:
        assert await store.list_approvals(db, thread.id) == []


async def test_an_mcp_approval_goes_through_the_gate(crt, sessions, owner, codex, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    codex.plan({"type": "mcp"})
    thread = await codex_thread(crt, sessions, owner, mode="ask")
    await crt.send_turn(thread.id, "search")
    approval = await pending_approval(sessions, thread.id)
    assert approval.tool == "mcp:docs"
    assert await crt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    assert codex.answers()[0]["result"] == {"action": "decline"}


async def test_an_unknown_request_is_refused(crt, sessions, owner, codex, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", True)
    codex.plan({"type": "unknown_request"})
    thread = await codex_thread(crt, sessions, owner, mode="ask")
    await crt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    assert codex.answers()[0]["error"]["code"] == -32601


async def test_a_lost_codex_thread_breaks_the_session(crt, sessions, owner, codex):
    thread = await codex_thread(crt, sessions, owner)
    async with sessions() as db:
        await store.set_session_state(db, thread.id, "active")
        row = await db.get(Thread, thread.id)
        row.provider_state = {"codex_thread_id": "gone"}
        await db.commit()
    await crt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    failed = data_of(await events_of(sessions, thread.id), "turn.failed")[0]
    assert failed["error_code"] == "session_resume_failed"
    assert (await reload(sessions, Thread, thread.id)).session_state == "broken"
    assert codex.received("turn/start") == []
    # A reset clears the old Codex thread, and the next turn starts a new one.
    await crt.reset_session(thread.id)
    assert (await reload(sessions, Thread, thread.id)).provider_state is None


async def test_active_without_a_codex_thread_is_broken_not_fresh(crt, sessions, owner, codex):
    thread = await codex_thread(crt, sessions, owner)
    async with sessions() as db:
        await store.set_session_state(db, thread.id, "active")
    await crt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    assert data_of(await events_of(sessions, thread.id), "turn.failed")[0]["error_code"] == "session_resume_failed"
    assert codex.received("thread/start") == []


async def test_a_policy_codex_did_not_apply_fails_closed(crt, sessions, owner, codex):
    codex.plan({"type": "chat"}, policy_mismatch=True)
    thread = await codex_thread(crt, sessions, owner)
    await crt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    assert data_of(await events_of(sessions, thread.id), "turn.failed")[0]["error_code"] == "codex_policy_mismatch"
    assert codex.received("turn/start") == []


async def test_stop_interrupts_and_keeps_the_partial_text(crt, sessions, owner, codex):
    codex.plan({"type": "slow"})
    thread = await codex_thread(crt, sessions, owner)
    await crt.send_turn(thread.id, "long one")

    async def typing():
        return bool(codex.received("turn/start"))

    await wait_until(typing)
    import asyncio

    await asyncio.sleep(0.5)
    await crt.stop_turn(thread.id)
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    assert data_of(events, "turn.cancelled")[0]["error_code"] == "stopped"
    assert {"text": "partial words", "incomplete": True} in data_of(events, "assistant.message")
    assert codex.received("turn/interrupt")
    # The session survives a stop.
    row = await reload(sessions, Thread, thread.id)
    assert row.session_state == "active" and row.provider_state["codex_thread_id"]


async def test_a_failed_turn_reports_codex_error(crt, sessions, owner, codex):
    codex.plan({"type": "fail", "message": "usage limit reached"})
    thread = await codex_thread(crt, sessions, owner)
    await crt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    failed = data_of(await events_of(sessions, thread.id), "turn.failed")[0]
    # A limit the engine reports is classified for every engine (engine_errors.py).
    assert failed["error_code"] == "engine_limited"
    assert failed["error"].startswith("Codex has reached its usage limit (Codex says: \"usage limit reached\")")


async def test_a_crash_mid_turn_fails_with_the_cli_error(crt, sessions, owner, codex):
    codex.plan({"type": "crash"})
    thread = await codex_thread(crt, sessions, owner)
    await crt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    failed = data_of(events, "turn.failed")[0]
    assert failed["error_code"] == "cli_error" and "codex crashed" in failed["error"]
    assert {"text": "half", "incomplete": True} in data_of(events, "assistant.message")


async def test_probe_reports_signed_in_with_live_models(codex):
    probe = await CodexAdapter().probe()
    assert probe.ready and probe.installed and probe.signed_in is True
    assert probe.version == "codex-cli 0.162.1"
    assert [(m.id, m.default) for m in probe.models] == [("gpt-fake", True), ("gpt-fake-mini", False)]


async def test_probe_reports_signed_out(codex):
    codex.plan(login="out")
    probe = await CodexAdapter().probe()
    assert not probe.ready and probe.installed and probe.signed_in is False
    assert "codex login" in probe.reason
    assert "example.com" not in json.dumps(probe.__dict__, default=str)


async def test_probe_with_a_hanging_login_check_gives_up(codex):
    codex.plan(login="hang")
    adapter = CodexAdapter()
    adapter.login_timeout = 0.5
    import time

    started = time.monotonic()
    probe = await adapter.probe()
    assert time.monotonic() - started < 5
    assert not probe.ready and probe.signed_in is None


async def test_probe_without_codex(monkeypatch):
    monkeypatch.setattr(codex_adapter, "resolve_codex_executable", lambda: None)
    probe = await CodexAdapter().probe()
    assert not probe.installed and not probe.ready and "not found" in probe.reason


async def test_team_mcp_create_uses_one_use_gate_with_host_tools_off(crt, sessions, owner, codex, monkeypatch):
    from app.team import service
    from app.db.models import Approval, TeamSetupRequest
    from sqlalchemy import select
    monkeypatch.setattr(settings, 'TEAM_ENABLED', True)
    monkeypatch.setattr(settings, 'TEAM_LEAD_AUTO', True)
    async with sessions() as db:
        lead = await agent_store.create_agent(db, owner_id=owner, name='Coordinator', provider='codex')
        await service.set_lead(db, owner, lead.id)
    codex.plan({'type': 'team', 'tool': 'create_agent', 'arguments': {'name': 'File reader', 'instructions': 'Summarize local files in concise bullets.'}})
    thread = await crt.create_thread(provider='codex', agent_id=lead.id)
    await crt.send_turn(thread.id, 'Create a file reader agent')
    await finished(sessions, thread.id)
    started = codex.received('thread/start')[0]['params']
    assert started['config']['mcp_servers']['team']['default_tools_approval_mode'] == 'prompt'
    assert started['sandbox'] == 'read-only'  # team access never enables host writes
    assert started['approvalPolicy'] == 'untrusted'  # the CLI must be able to ask about MCP calls
    async with sessions() as db:
        worker = await db.scalar(select(Agent).where(Agent.owner_id == owner, Agent.name == 'File reader'))
        assert worker is not None and worker.approval_mode == 'ask'
        approved = await db.scalar(select(Approval).where(Approval.turn_id == (await crt_turns(sessions, thread.id))[0], Approval.tool == 'mcp__team__create_agent'))
        assert approved.status == 'allowed' and approved.consumed_at is not None
        assert await db.scalar(select(TeamSetupRequest).where(TeamSetupRequest.owner_id == owner))
    events = await events_of(sessions, thread.id)
    assert data_of(events, 'team.setup.applied')[0]['results'][0]['changes']


@pytest.mark.parametrize('mode,origin', [('plan', 'person'), ('ask', 'outside'), ('ask', 'routine')])
async def test_codex_team_mutations_preserve_policy_floor(crt, sessions, owner, codex, monkeypatch, mode, origin):
    from app.team import service
    from sqlalchemy import select
    monkeypatch.setattr(settings, 'TEAM_ENABLED', True)
    async with sessions() as db:
        lead = await agent_store.create_agent(db, owner_id=owner, name='Coordinator', provider='codex', approval_mode=mode)
        await service.set_lead(db, owner, lead.id)
    codex.plan({'type': 'team', 'tool': 'create_agent', 'arguments': {'name': 'Forbidden worker', 'instructions': 'Read files.'}})
    thread = await crt.create_thread(provider='codex', agent_id=lead.id)
    await crt.send_turn(thread.id, 'create an agent', team_origin=origin)
    await finished(sessions, thread.id)
    assert codex.answers()[0]['result']['action'] == 'decline'
    async with sessions() as db:
        assert not await db.scalar(select(Agent).where(Agent.owner_id == owner, Agent.name == 'Forbidden worker'))
