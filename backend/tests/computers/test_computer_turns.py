"""A turn of an agent with a computer, end to end: the runtime attaches the
computer, the fake Claude CLI (a real child process) asks about a computer tool
call, the approval gate records it, and on Allow the CLI calls the computer's
real MCP server, which runs the command through the fake docker in the mounted
folder."""

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.computers.provider import DockerProvider
from app.computers.service import ComputerService
from app.core.config import settings
from app.db.models import Agent, Computer
from app.services import approval_allowlist, local_claude_runner, thread_runtime
from app.services import thread_store as store
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.computers.harness import docker  # noqa: F401
from tests.services.thread_harness import (  # noqa: F401
    FAKE_CLI,
    Cli,
    ReadyClaude,
    cleanup,
    events_of,
    finished,
    owner,
    pending_approval,
    sessions,
    wait_until,
)


@pytest.fixture
def cli(monkeypatch, tmp_path):
    fake = Cli(tmp_path / "plan.json")
    launches: list[dict] = []
    monkeypatch.setattr(local_claude_runner, "resolve_claude_executable", lambda: tmp_path / "claude")
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)

    original = local_claude_runner.build_args

    def fake_build_args(executable, session_id, tools=False, resume=False, approval_mode=None,
                        model=None, system_prompt=None, computer=None, **_extra):
        # What the real CLI would have been given, kept for the test to read.
        real = original(
            executable, session_id, tools=tools, resume=resume, approval_mode=approval_mode,
            model=model, system_prompt=system_prompt, computer=computer, **_extra,
        )
        launches.append({"args": real, "system_prompt": system_prompt})
        mcp = fake.plan.parent / "mcp.json"
        if computer is not None:
            mcp.write_text(local_claude_runner.computer_mcp_config(computer))
        elif mcp.exists():
            mcp.unlink()
        args = [sys.executable, str(FAKE_CLI), str(fake.plan), "resume" if resume else "new", session_id,
                "1" if tools else "0"]
        return args + [approval_mode] if approval_mode else args

    monkeypatch.setattr(local_claude_runner, "build_args", fake_build_args)
    fake.launches = launches
    return fake


@pytest.fixture
async def rt(sessions, owner, monkeypatch, docker, tmp_path):  # noqa: F811
    monkeypatch.setattr(settings, "LOCAL_TOOLS_ENABLED", False)  # a computer turns tools on by itself
    monkeypatch.setattr(settings, "THREAD_HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setattr(thread_runtime, "APPROVAL_POLL_SECONDS", 0.1)
    runtime = ThreadRuntime(
        hub=ThreadHub(), adapters={"claude": ReadyClaude()}, status_ttl=0, _session_factory=sessions, owner_id=owner
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


async def agent_with_computer(rt, sessions, docker, *, mode="ask", image_present=True):  # noqa: F811
    async with sessions() as db:
        agent = Agent(owner_id=rt.owner_id, name="Builder " + uuid4().hex[:6], approval_mode=mode)
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
    if image_present:
        docker.add_image()
    workspace = local_claude_runner.workspace_dir() / "agents" / str(agent.id)
    workspace.mkdir(parents=True, exist_ok=True)
    computer, _ = await rt.computers.create_for_agent(agent, workspace=str(workspace.resolve()))
    thread = await rt.create_thread(agent_id=agent.id)
    return agent, computer, thread, workspace


def tool_step(name, tool_input):
    return {"type": "tool", "tool_name": "mcp__computer__" + name, "tool_input": tool_input}


async def test_an_approved_command_runs_in_the_computer_not_on_the_host(rt, sessions, cli, docker):  # noqa: F811
    agent, computer, thread, workspace = await agent_with_computer(rt, sessions, docker)
    cli.script(tool_step("exec", {"command": "echo built > out.txt && cat out.txt"}))
    await rt.send_turn(thread.id, "build it")
    approval = await pending_approval(sessions, thread.id)
    assert approval.tool == "mcp__computer__exec"
    assert approval.kind == "command"
    assert approval.summary.startswith("computer: echo built")
    assert not (workspace / "out.txt").exists()  # nothing ran before the Allow
    assert await rt.answer_approval(approval.id, "allow")
    await finished(sessions, thread.id)

    result = cli.log("mcp_result")[0]
    assert not result["is_error"] and "exit code: 0" in result["content"] and "built" in result["content"]
    assert (workspace / "out.txt").read_text() == "built\n"
    name = rt.computers.spec(computer).name
    assert any(c[0] == "exec" and name in c for c in docker.calls())

    launch = cli.launches[-1]
    args = launch["args"]
    assert args[args.index("--tools") + 1] == ""  # no host tools at all
    config = json.loads(args[args.index("--mcp-config") + 1])
    # the computer is the only source of host-like tools; the read-only memory search rides along
    assert sorted(config["mcpServers"]) == ["computer", "memory"]
    assert "--strict-mcp-config" in args and "--permission-prompt-tool" in args
    assert "own computer" in launch["system_prompt"]
    assert cli.log("start")[0]["tools"] is True
    assert not rt.computers.busy(computer.id)
    assert (await rt.computers.get(computer.id)).state == "running"
    assert rt.computers.last_command(computer.id)["subject"].startswith("echo built")


async def test_a_denied_command_never_runs(rt, sessions, cli, docker):  # noqa: F811
    _, _, thread, workspace = await agent_with_computer(rt, sessions, docker)
    cli.script(tool_step("exec", {"command": "touch denied.txt"}))
    await rt.send_turn(thread.id, "try")
    approval = await pending_approval(sessions, thread.id)
    assert await rt.answer_approval(approval.id, "deny")
    await finished(sessions, thread.id)
    assert cli.log("mcp_result") == []
    assert not (workspace / "denied.txt").exists()
    assert "exec" not in docker.commands()


async def test_reads_go_through_the_gate_without_a_card(rt, sessions, cli, docker):  # noqa: F811
    _, _, thread, workspace = await agent_with_computer(rt, sessions, docker)
    (workspace / "README.md").write_text("hello from the folder\n")
    cli.script(tool_step("read_file", {"path": "README.md"}))
    await rt.send_turn(thread.id, "read")
    await finished(sessions, thread.id)
    assert cli.log("mcp_result")[0]["content"] == "hello from the folder\n"
    async with sessions() as db:
        approvals = await store.list_approvals(db, thread.id)
    assert [(a.tool, a.status) for a in approvals] == [("mcp__computer__read_file", "allowed")]
    resolved = [e.data for e in await events_of(sessions, thread.id) if e.type == "approval.resolved"]
    assert resolved[0]["by"] == "computer_read"


async def test_plan_mode_refuses_commands_without_asking(rt, sessions, cli, docker):  # noqa: F811
    _, _, thread, workspace = await agent_with_computer(rt, sessions, docker, mode="plan")
    cli.script(tool_step("exec", {"command": "touch planned.txt"}))
    await rt.send_turn(thread.id, "plan")
    await finished(sessions, thread.id)
    assert cli.log("reply")[0]["reply"]["response"]["response"]["behavior"] == "deny"
    assert not (workspace / "planned.txt").exists()


async def test_a_saved_command_rule_allows_the_same_command_in_the_computer(rt, sessions, cli, docker):  # noqa: F811
    _, _, thread, workspace = await agent_with_computer(rt, sessions, docker)
    async with sessions() as db:
        await approval_allowlist.add_rule(db, rt.owner_id, approval_allowlist.EXACT, "ls", str(workspace.resolve()))
    try:
        cli.script(tool_step("exec", {"command": "ls"}))
        await rt.send_turn(thread.id, "list")
        await finished(sessions, thread.id)
        assert not cli.log("mcp_result")[0]["is_error"]
        resolved = [e.data for e in await events_of(sessions, thread.id) if e.type == "approval.resolved"]
        assert resolved[0]["by"] == "saved_rule"
    finally:
        async with sessions() as db:
            for rule in await approval_allowlist.list_rules(db, rt.owner_id):
                await approval_allowlist.delete_rule(db, rt.owner_id, rule.id)


async def test_a_computer_that_cannot_start_fails_the_turn_and_never_runs_on_the_host(rt, sessions, cli, docker):  # noqa: F811
    _, _, thread, _ = await agent_with_computer(rt, sessions, docker)
    docker.mode("down")
    cli.script(tool_step("exec", {"command": "touch host.txt"}))
    await rt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    events = await events_of(sessions, thread.id)
    failed = [e.data for e in events if e.type == "turn.failed"][0]
    assert failed["error_code"] == "computer_unavailable"
    assert cli.launches == []  # the CLI was never started


async def test_a_missing_image_is_never_downloaded_by_a_turn(rt, sessions, cli, docker):  # noqa: F811
    _, _, thread, _ = await agent_with_computer(rt, sessions, docker, image_present=False)
    cli.script({"type": "chat"})
    await rt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    failed = [e.data for e in await events_of(sessions, thread.id) if e.type == "turn.failed"][0]
    assert failed["error_code"] == "computer_unavailable" and "Prepare the image" in failed["error"]
    assert "pull" not in docker.commands()


async def test_an_agent_without_a_computer_runs_as_before(rt, sessions, cli, docker):  # noqa: F811
    async with sessions() as db:
        agent = Agent(owner_id=rt.owner_id, name="Plain " + uuid4().hex[:6])
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
    thread = await rt.create_thread(agent_id=agent.id)
    cli.script({"type": "chat"})
    await rt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    args = cli.launches[-1]["args"]
    # No computer, no host tools: the only thing mounted is the agent's read-only
    # memory search, asked about like any tool (docs/MEMORY.md).
    assert docker.calls() == []
    assert args[args.index("--tools") + 1] == ""
    config = json.loads(args[args.index("--mcp-config") + 1])
    assert list(config["mcpServers"]) == ["memory"]


def test_build_args_with_a_computer(tmp_path):
    from app.computers.provider import McpLaunch

    launch = McpLaunch("/usr/bin/python3", ["-m", "app.computers.mcp_server", "--container", "c"], {"PYTHONPATH": "/x"})
    args = local_claude_runner.build_args(Path("/bin/claude"), "sid", tools=True, computer=launch, approval_mode="accept_edits")
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    server = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]["computer"]
    assert server == {"type": "stdio", "command": "/usr/bin/python3",
                      "args": ["-m", "app.computers.mcp_server", "--container", "c"], "env": {"PYTHONPATH": "/x"}}
    assert "Bash" not in " ".join(args)
