"""The read-only past-conversation search (session_search, session_read) on Codex
and ACP agents, through real child processes playing those engines
(docs/MEMORY.md, "Searching past conversations")."""

from app.services import agent_store, memory_bridge
from app.services.provider_adapter import Capabilities
from tests.services.test_acp_adapter import FAKE, FakeAcp, acp, acp_thread, art  # noqa: F401
from tests.services.test_acp_adapter import data_of as acp_data
from tests.services.test_codex_adapter import FakeCodex, codex, crt  # noqa: F401
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401


def test_a_memory_call_is_recognised_only_for_the_two_tools_of_the_memory_server():
    ok = memory_bridge.is_memory_call
    assert ok(server="memory", tool="session_search") and ok(server="memory", tool="session_read")
    assert not ok(server="memory", tool="delete_everything")
    assert not ok(server="other", tool="session_search")
    for title in ("session_search (memory MCP Server)", "mcp__memory__session_search", "memory/session_read", "memory_session_search"):
        assert ok(title=title, raw={"query": "audit"}), title
    assert not ok(title="Run rm -rf (memory)", raw={})
    assert not ok(title="session_search", raw={"query": "x"})  # not named as the memory server's
    assert not ok(title="session_search (memory)", raw={"command": "rm -rf /"})  # arguments those tools never take
    assert not ok(title="memory session_search " + "x" * 300)


def test_only_codex_and_acp_set_the_memory_tools_flag_without_the_team_tools():
    from app.services.acp_adapter import AcpAdapter
    from app.services.codex_adapter import CodexAdapter
    from app.services.openai_chat_adapter import OpenAIChatAdapter

    for adapter in (CodexAdapter, AcpAdapter):
        caps = adapter.capabilities
        assert caps.memory_tools and not caps.mcp and caps.can_search_history
    assert not OpenAIChatAdapter.capabilities.can_search_history
    assert Capabilities().can_search_history is False and Capabilities(mcp=True).can_search_history


async def _agent(sessions, owner, provider, **kwargs):
    async with sessions() as db:
        return await agent_store.create_agent(db, owner_id=owner, name="Scout", provider=provider, **kwargs)


# --- Codex ----------------------------------------------------------------------------------


async def test_codex_gets_the_memory_server_for_the_thread_only(crt, sessions, owner, codex):
    agent = await _agent(sessions, owner, "codex", instructions="Be brief.")
    thread = await crt.create_thread(provider="codex", agent_id=agent.id)
    await crt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    params = codex.received("thread/start")[0]["params"]
    server = params["config"]["mcp_servers"]["memory"]
    assert server["args"][-2] == "--mailbox" and "open-dots-memory-" in server["args"][-1]
    assert server["enabled_tools"] == ["session_search", "session_read"]
    assert server["default_tools_approval_mode"] == "approve"  # read-only: no card
    assert params["developerInstructions"].startswith("Be brief.") and memory_bridge.TOOL_NOTE in params["developerInstructions"]
    # A second turn resumes the thread with the same server.
    await crt.send_turn(thread.id, "again")
    await finished(sessions, thread.id, count=2)
    assert "memory" in codex.received("thread/resume")[0]["params"]["config"]["mcp_servers"]


async def test_codex_without_an_agent_or_with_notes_off_mounts_nothing(crt, sessions, owner, codex):
    plain = await crt.create_thread(provider="codex")
    await crt.send_turn(plain.id, "hi")
    await finished(sessions, plain.id)
    off = await _agent(sessions, owner, "codex", memory_enabled=False)
    thread = await crt.create_thread(provider="codex", agent_id=off.id)
    await crt.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    assert all("config" not in m["params"] for m in codex.received("thread/start"))
    assert all("developerInstructions" not in m["params"] for m in codex.received("thread/start"))


# --- ACP ------------------------------------------------------------------------------------


async def test_an_acp_agent_gets_the_memory_server_and_the_note(art, sessions, owner, acp):
    agent = await _agent(sessions, owner, "acp")
    thread = await art.create_thread(provider="acp", agent_id=agent.id)
    await art.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    (server,) = acp.received("session/new")[0]["params"]["mcpServers"]
    assert server["name"] == "memory" and server["args"][-2] == "--mailbox"
    assert all(set(e) == {"name", "value"} for e in server["env"])
    assert memory_bridge.TOOL_NOTE in acp.prompts()[0]


async def test_an_acp_agent_without_notes_gets_no_server(art, sessions, owner, acp):
    agent = await _agent(sessions, owner, "acp", memory_enabled=False)
    thread = await art.create_thread(provider="acp", agent_id=agent.id)
    await art.send_turn(thread.id, "hi")
    await finished(sessions, thread.id)
    assert acp.received("session/new")[0]["params"]["mcpServers"] == []
    assert memory_bridge.TOOL_NOTE not in acp.prompts()[0]


async def test_a_memory_call_is_allowed_without_a_card_and_anything_else_still_is_not(art, sessions, owner, acp):
    agent = await _agent(sessions, owner, "acp")
    steps = [
        {"type": "permission", "kind": "other", "title": "session_search (memory MCP Server)", "raw": {"query": "audit"}},
        {"type": "permission", "kind": "other", "title": "session_search (memory MCP Server)", "raw": {"command": "rm -rf /"}},
        {"type": "permission", "kind": "execute", "command": "ls"},
    ]
    thread = await art.create_thread(provider="acp", agent_id=agent.id)
    for n, step in enumerate(steps, 1):
        acp.plan(step)
        (acp.folder / "prompts").unlink(missing_ok=True)
        await art.send_turn(thread.id, f"go {n}")
        await finished(sessions, thread.id, count=n)
    # Host tools are off here, so everything else is refused; the memory call alone went through.
    assert [a["result"]["outcome"]["optionId"] for a in acp.answers()] == ["once", "no", "no"]
    from app.services import thread_store as store

    async with sessions() as db:
        assert await store.list_approvals(db, thread.id) == []  # no card for any of them
