"""A turn of an agent with a computer on the Codex engine: the runtime attaches the
computer, the fake `codex` app-server (a real child process) is given the computer's
MCP server and its own tools switched off, asks about a computer call, the approval
gate records it, and on Allow the real computer MCP server runs the command through
the fake docker in the mounted folder."""

import json
import stat
import sys
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.computers.provider import DockerProvider
from app.computers.service import ComputerService
from app.core.config import settings
from app.db.models import Agent, Computer
from app.services import codex_adapter, local_claude_runner, thread_runtime
from app.services import thread_store as store
from app.services.codex_adapter import CodexAdapter
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.computers.harness import docker  # noqa: F401
from tests.services.test_codex_adapter import FAKE, FakeCodex
from tests.services.thread_harness import (  # noqa: F401
    cleanup,
    events_of,
    finished,
    owner,
    pending_approval,
    sessions,
)


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
    return FakeCodex(folder)


@pytest.fixture
async def rt(sessions, owner, monkeypatch, docker, tmp_path):  # noqa: F811
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)  # a computer turns tools on by itself
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(thread_runtime, "APPROVAL_POLL_SECONDS", 0.1)
    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"codex": CodexAdapter()}, status_ttl=0, _session_factory=sessions, owner_id=owner
    )
    runtime.computers = ComputerService(
        providers={"docker": DockerProvider(docker=str(docker.path))},
        owner_id=owner,
        root=tmp_path / "computers",
        _session_factory=runtime.session,
    )
    yield runtime
    await runtime.shutdown()


@pytest.fixture(autouse=True)
async def rows_cleanup(sessions, owner):  # noqa: F811
    yield
    async with sessions() as db:
        await db.execute(delete(Computer).where(Computer.owner_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


async def codex_agent_with_computer(rt, sessions, docker, *, mode="ask"):  # noqa: F811
    async with sessions() as db:
        agent = Agent(owner_id=rt.owner_id, name="Builder " + uuid4().hex[:6], approval_mode=mode, provider="codex")
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
    docker.add_image()
    workspace = local_claude_runner.workspace_dir() / "agents" / str(agent.id)
    workspace.mkdir(parents=True, exist_ok=True)
    computer, _ = await rt.computers.create_for_agent(agent, workspace=str(workspace.resolve()))
    thread = await rt.create_thread(agent_id=agent.id)
    return agent, computer, thread, workspace


def step(tool, arguments):
    return {"type": "computer", "tool": tool, "arguments": arguments}


async def test_an_approved_command_runs_in_the_computer_not_on_the_host(rt, sessions, codex, docker):  # noqa: F811
    _, computer, thread, workspace = await codex_agent_with_computer(rt, sessions, docker)
    codex.plan(step("exec", {"command": "echo built > out.txt && cat out.txt"}))
    await rt.send_turn(thread.id, "build it")
    approval = await pending_approval(sessions, thread.id)
    # The card is the one Claude's computer calls get.
    assert approval.tool == "mcp__computer__exec" and approval.kind == "command"
    assert approval.summary.startswith("computer: echo built")
    assert not (workspace / "out.txt").exists()  # nothing ran before the Allow
    assert await rt.answer_approval(approval.id, "allow")
    await finished(sessions, thread.id)

    result = next(e for e in codex.log() if e.get("event") == "mcp_result")
    assert not result["is_error"] and "exit code: 0" in result["content"] and "built" in result["content"]
    assert (workspace / "out.txt").read_text() == "built\n"
    assert any(c[0] == "exec" and rt.computers.spec(computer).name in c for c in docker.calls())
    assert not rt.computers.busy(computer.id)


async def test_codex_is_launched_with_its_own_tools_off_and_only_the_computer_mounted(rt, sessions, codex, docker):  # noqa: F811
    _, _, thread, _ = await codex_agent_with_computer(rt, sessions, docker)
    codex.plan({"type": "chat", "text": "hi"}, user_mcp={"theirs": {"command": "x"}})
    await rt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    init = next(e for e in codex.log() if e.get("event") == "initialize")["params"]
    assert init["capabilities"] == {"experimentalApi": True}
    started = codex.received("thread/start")[0]["params"]
    config = started["config"]
    assert config["features"] == {"shell_tool": False, "unified_exec": False, "view_image": False}
    assert config["web_search"] == "disabled"
    servers = config["mcp_servers"]
    assert servers["computer"]["default_tools_approval_mode"] == "prompt"
    assert servers["theirs"] == {"enabled": False}  # the person's own servers would run on the host
    assert "own computer" in started["developerInstructions"] and "shell and file tools are turned off" in started["developerInstructions"]
    # Nothing of Codex's may write, and the turn has no execution environment.
    assert (started["approvalPolicy"], started["sandbox"]) == ("untrusted", "read-only")
    turn = codex.received("turn/start")[0]["params"]
    assert turn["environments"] == [] and turn["sandboxPolicy"] == {"type": "readOnly"}


async def test_a_denied_command_never_runs(rt, sessions, codex, docker):  # noqa: F811
    _, _, thread, workspace = await codex_agent_with_computer(rt, sessions, docker)
    codex.plan(step("exec", {"command": "touch denied.txt"}))
    await rt.send_turn(thread.id, "try")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    assert not any(e.get("event") == "mcp_result" for e in codex.log())
    assert codex.answers()[0]["result"] == {"action": "decline"}
    assert not (workspace / "denied.txt").exists()
    assert "exec" not in docker.commands()


async def test_without_a_permit_the_computer_refuses_even_if_codex_calls_it(rt, sessions, codex, docker, monkeypatch):  # noqa: F811
    from app.computers import grants

    monkeypatch.setattr(grants, "issue", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    _, _, thread, workspace = await codex_agent_with_computer(rt, sessions, docker)
    codex.plan(step("exec", {"command": "touch nopermit.txt"}))
    await rt.send_turn(thread.id, "try")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "allow")
    await finished(sessions, thread.id)
    assert codex.answers()[0]["result"] == {"action": "decline"}  # an Allow that could not be permitted is a no
    assert not (workspace / "nopermit.txt").exists()


async def test_reads_go_through_the_gate_without_a_card(rt, sessions, codex, docker):  # noqa: F811
    _, _, thread, workspace = await codex_agent_with_computer(rt, sessions, docker)
    (workspace / "README.md").write_text("hello from the folder\n")
    codex.plan(step("read_file", {"path": "README.md"}))
    await rt.send_turn(thread.id, "read")
    await finished(sessions, thread.id)
    result = next(e for e in codex.log() if e.get("event") == "mcp_result")
    assert result["content"] == "hello from the folder\n"
    async with sessions() as db:
        approvals = await store.list_approvals(db, thread.id)
    assert [(a.tool, a.status) for a in approvals] == [("mcp__computer__read_file", "allowed")]


async def test_plan_mode_refuses_commands_without_asking(rt, sessions, codex, docker):  # noqa: F811
    _, _, thread, workspace = await codex_agent_with_computer(rt, sessions, docker, mode="plan")
    codex.plan(step("exec", {"command": "touch planned.txt"}))
    await rt.send_turn(thread.id, "plan")
    await finished(sessions, thread.id)
    assert codex.answers()[0]["result"] == {"action": "decline"}
    assert not (workspace / "planned.txt").exists()


async def test_codex_using_its_own_shell_stops_the_turn(rt, sessions, codex, docker):  # noqa: F811
    _, _, thread, workspace = await codex_agent_with_computer(rt, sessions, docker)
    codex.plan({"type": "command", "command": "touch host.txt"})
    await rt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    failed = [e.data for e in await events_of(sessions, thread.id) if e.type == "turn.failed"]
    assert failed and failed[0]["error_code"] == "codex_confinement_failed"
    assert not (workspace / "out.txt").exists() and not (workspace / "host.txt").exists()
    assert "exec" not in docker.commands()


async def test_another_server_is_never_approved_during_a_computer_turn(rt, sessions, codex, docker):  # noqa: F811
    _, _, thread, _ = await codex_agent_with_computer(rt, sessions, docker)
    codex.plan({"type": "mcp"})  # the docs server asks to run "search"
    await rt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    assert codex.answers()[0]["result"] == {"action": "decline"}


async def test_native_desktop_control_stays_claude_only(rt, sessions, codex):
    from types import SimpleNamespace

    from app.services.provider_adapter import TurnContext

    ctx = TurnContext.__new__(TurnContext)
    ctx.__dict__.update(computer=SimpleNamespace(provider="native"), provider_state=None, resume=False)
    outcome = await CodexAdapter().run_turn(ctx, None, None)
    assert outcome.status == "failed" and outcome.error_code == "engine_without_computers"
