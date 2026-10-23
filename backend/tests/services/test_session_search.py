"""Past-conversation search: the query, its scope and privacy rules, and the
MCP tool end to end (a real server process talking to the bridge)
(docs/MEMORY.md)."""

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app.db.models import Event, Thread
from app.services import agent_memory, memory_log, memory_topics, session_search, tool_policy
from app.services.memory_bridge import MemoryBridge
from tests.services.test_memory_log_brief import harness, make_agent, say  # noqa: F401
from tests.services.thread_harness import cleanup, finished, owner, sessions  # noqa: F401


def test_since_and_until_forms():
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    assert session_search.parse_since("24h", now) == now - timedelta(hours=24)
    assert session_search.parse_since("2w", now) == now - timedelta(weeks=2)
    assert session_search.parse_since("", now) is None
    day = session_search.parse_since("2026-10-01", now).astimezone()
    assert (day.year, day.month, day.day, day.hour) == (2026, 10, 1, 0)
    end = session_search.parse_until("2026-10-01", now).astimezone()
    assert (end.day, end.hour, end.minute) == (1, 23, 59)
    assert session_search.parse_until("24h", now) == now - timedelta(hours=24)
    with pytest.raises(session_search.BadTime):
        session_search.parse_since("sometime", now)


async def search(rt, agent, **kwargs):
    async with rt.session() as db:
        return await session_search.search(db, owner_id=rt.owner_id, agent_id=agent.id, current_thread_id=kwargs.pop("current", None), **kwargs)


async def test_words_time_scope_and_other_agents(harness, sessions, owner):
    rt, _ = harness
    scout = await make_agent(sessions, owner)
    other = await make_agent(sessions, owner, name="Other")
    one = await say(rt, sessions, scout, "Plan the pricing audit for March", title="Pricing")
    await say(rt, sessions, scout, "Book flights to Goa", title="Trip")
    await say(rt, sessions, other, "Pricing audit secret of the other agent")
    hits = (await search(rt, scout, query="pricing audit")).conversations
    assert {h.thread_id for h in hits} == {one.id} and all("other agent" not in h.snippet for h in hits)
    assert all(h.private and h.where.startswith("1:1 chat") for h in hits)
    assert {h.speaker for h in hits} == {"the person", "you"}
    # every word must match
    assert (await search(rt, scout, query="pricing goa")).conversations == []
    # time only, newest first, and the current conversation is marked
    recent = (await search(rt, scout, since=datetime.now(UTC) - timedelta(hours=1), current=one.id)).conversations
    assert recent[0].at >= recent[-1].at and any(h.current for h in recent)
    assert (await search(rt, scout, since=datetime.now(UTC) + timedelta(hours=1))).conversations == []
    # archived conversations are left out
    async with sessions() as db:
        await db.execute(update(Thread).where(Thread.id == one.id).values(archived_at=datetime.now(UTC)))
        await db.commit()
    assert (await search(rt, scout, query="pricing audit")).conversations == []


async def test_room_timelines_and_reads(harness, sessions, owner):
    rt, _ = harness
    lead = await make_agent(sessions, owner, name="Lead")
    outsider = await make_agent(sessions, owner, name="Outsider")
    room = await rt.rooms.create({"name": "Standup", "member_ids": [lead.id], "default_responder_kind": "everyone"})
    await rt.rooms.post(room["id"], "Launch moved to Friday")
    await rt.rooms.work(room["id"])
    hits = (await search(rt, lead, query="launch friday")).conversations
    assert hits and hits[0].where == 'room "Standup"' and not hits[0].private
    assert (await search(rt, outsider, query="launch friday")).conversations == []
    async with rt.session() as db:
        message = await session_search.read(db, owner_id=owner, agent_id=lead.id, thread_id=hits[0].thread_id, seq=hits[0].seq)
        assert "Launch moved to Friday" in message["text"]
        assert await session_search.read(db, owner_id=owner, agent_id=outsider.id, thread_id=hits[0].thread_id, seq=hits[0].seq) is None


async def test_memory_files_without_dead_entries_but_with_archive_and_logs(harness, sessions, owner):
    rt, _ = harness
    agent = await make_agent(sessions, owner)
    agent_memory.write_memory(agent.id, "- 2026-10-01 · The garden shed is blue\n")
    memory_topics.write_topic(agent.id, "garden.md", "\n".join([
        "- 2026-01-01 · ~~The garden gate is red~~ · superseded 2026-02-01",
        "- 2026-01-01 · The garden gate is green",
        "- 2026-01-01 · The garden hose is old · until 2026-02-01",
    ]) + "\n")
    memory_topics.write_topic(agent.id, "archive.md", "- 2026-01-01 · The garden gnome moved · moved 2026-03-01\n")
    memory_log.append(agent.id, "Watered the garden", source='chat "A"')
    paths = {m.path: m.snippet for m in (await search(rt, agent, query="garden")).memory}
    text = " ".join(paths.values())
    assert "shed is blue" in text and "gate is green" in text and "gnome moved" in text and "Watered the garden" in text
    assert "gate is red" not in text and "hose" not in text
    assert (await search(rt, agent, query="garden", scope="conversations")).memory == []
    assert (await search(rt, agent, query="garden", scope="memory")).conversations == []
    assert (await search(rt, agent, query="garden", memory=False)).memory == []


def test_the_tool_is_allowed_without_a_card_and_unknown_ones_are_not():
    run = SimpleNamespace(ctx=SimpleNamespace(team=None))
    assert tool_policy.automatic("mcp__memory__session_search", "ask", run, {}) == ("allowed", "memory_read")
    assert tool_policy.automatic("mcp__memory__delete_everything", "ask", run, {})[0] == "denied"


async def test_the_mcp_tool_end_to_end_and_disclosure_in_a_room(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner, name="Lead")
    await say(rt, sessions, agent, "The vendor contract renews on 3 March", title="Contracts")
    seen = {}

    async def run_turn(ctx, sink, gate):
        # What the Claude adapter would do: start the server the context names.
        launch = ctx.memory_launch
        assert launch is not None
        env = {"PATH": "/usr/bin:/bin", **launch.env}
        proc = subprocess.Popen([launch.command, *launch.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True)

        async def rpc(method, params=None, id=1):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}) + "\n")
            proc.stdin.flush()
            line = await asyncio.get_running_loop().run_in_executor(None, proc.stdout.readline)
            return json.loads(line)["result"]

        try:
            await rpc("initialize")
            tools = {t["name"] for t in (await rpc("tools/list"))["tools"]}
            found = await rpc("tools/call", {"name": "session_search", "arguments": {"query": "vendor contract"}})
            seen.update(tools=tools, found=found)
            ids = found["content"][0]["text"].split("thread ")[1].split(" · message ")
            read = await rpc("tools/call", {"name": "session_read", "arguments": {"thread_id": int(ids[0]), "message_id": int(ids[1].split("]")[0])}})
            seen["read"] = read
            seen["bad"] = await rpc("tools/call", {"name": "session_search", "arguments": {}})
        finally:
            proc.kill()
        await sink.emit("assistant.message", {"text": "ok"})
        from app.services.provider_adapter import TurnOutcome

        return TurnOutcome("succeeded", result="ok")

    engine.run_turn = run_turn
    room = await rt.rooms.create({"name": "Standup", "member_ids": [agent.id], "default_responder_kind": "everyone"})
    await rt.rooms.post(room["id"], "What is up with contracts?")
    await rt.rooms.work(room["id"])
    assert seen["tools"] == {"session_search", "session_read"}
    text = seen["found"]["content"][0]["text"]
    assert "vendor contract renews on 3 March" in text and "private to this person" in text and not seen["found"]["isError"]
    assert "renews on 3 March" in seen["read"]["content"][0]["text"]
    assert seen["bad"]["isError"] is True
    notices = [m["text"] for m in await rt.rooms.messages(room["id"]) if m.get("kind") == "recall"]
    assert "Lead recalled 1 message from its private chat with you" in notices
