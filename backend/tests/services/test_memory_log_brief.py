"""Daily logs and the recent-work brief, through a real runtime with an
in-process engine (docs/MEMORY.md)."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db.models import Thread, Turn
from app.services import agent_memory, agent_store, local_claude_runner, memory_journal, memory_log, memory_topics, recent_work
from app.services.provider_adapter import Capabilities, ProviderStatus, TurnOutcome
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from tests.services.thread_harness import cleanup, finished, owner, sessions, wait_until  # noqa: F401


class Engine:
    key = "claude"
    capabilities = Capabilities(tools=True, approvals=True, mcp=True)
    custom_models = True

    def __init__(self):
        self.contexts = []
        self.fail = False
        self.tool = None
        self.edit = None

    def new_session_id(self):
        return None

    async def status(self):
        return ProviderStatus(ready=True)

    async def run_turn(self, ctx, sink, gate):
        self.contexts.append(ctx)
        if self.edit:
            self.edit(ctx)
        if self.tool:
            await sink.emit("tool.started", {"call_id": "c1", "name": self.tool, "input": {}})
            await sink.emit("tool.completed", {"call_id": "c1", "ok": True, "output": "ok"})
        if self.fail:
            return TurnOutcome("failed", error="boom", error_code="runner_error")
        reply = f"Done: {ctx.prompt[:40]} token=hunter2hunter2"
        await sink.emit("assistant.message", {"text": reply})
        return TurnOutcome("succeeded", result=reply)


@pytest.fixture
async def harness(sessions, owner, monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "TEAM_ENABLED", True)
    monkeypatch.setattr(settings, "MEMORY_CAPTURE_QUIET_SECONDS", 0)
    engine = Engine()
    runtime = ThreadRuntime(hub=ThreadHub(), adapters={"claude": engine}, owner_id=owner, _session_factory=sessions)
    yield runtime, engine
    await runtime.shutdown()
    for task in runtime.rooms.tasks.values():
        if not task.done():
            task.cancel()


async def make_agent(sessions, owner, name="Scout", **kwargs):
    async with sessions() as db:
        return await agent_store.create_agent(db, owner_id=owner, name=name, **kwargs)


async def say(rt, sessions, agent, text, title=None, thread=None):
    thread = thread or await rt.create_thread(agent_id=agent.id, title=title)
    count = (await _turns(sessions, thread.id)) + 1
    await rt.send_turn(thread.id, text)
    await finished(sessions, thread.id, count=count)
    await asyncio.sleep(0.05)
    return thread


async def _turns(sessions, thread_id):
    async with sessions() as db:
        return len((await db.execute(select(Turn).where(Turn.thread_id == thread_id))).scalars().all())


# --- the daily log ----------------------------------------------------------------------


def test_a_turn_becomes_one_line():
    line = memory_log.turn_line
    assert line("succeeded", "Sent the invoices", None, ["Bash", "Read", "Bash"]) == "Sent the invoices [tools: Bash, Read]"
    assert line("succeeded", "", None, []) is None
    assert line("failed", "partial", "runner_error", []) == "(turn failed: runner_error) partial"
    assert line("cancelled", None, "stopped", []) == "(stopped) no reply"
    assert len(line("succeeded", "x" * 500, None, [])) == memory_log.REPLY_CHARS


async def test_every_finished_turn_leaves_a_redacted_line_with_its_source_and_tools(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner)
    engine.tool = "Read"
    thread = await say(rt, sessions, agent, "Reconcile the March invoices", title="Invoices · March")
    engine.tool = None
    engine.fail = True
    await say(rt, sessions, agent, "Try again please", thread=thread)
    (day,) = memory_log.list_logs(agent.id)
    text = memory_log.read_log(agent.id, day["day"])
    first, second = text.strip().split("\n")
    assert first.startswith("- ") and ' · from chat "Invoices March" · Done: Reconcile the March invoices' in first
    assert "[tools: Read]" in first and "hunter2" not in text
    assert "(turn failed: runner_error) no reply" in second
    path = memory_log.logs_dir(agent.id) / f"{day['day']}.md"
    assert oct(os.stat(path).st_mode)[-3:] == "600"


async def test_memory_off_or_no_agent_means_no_log(harness, sessions, owner):
    rt, _ = harness
    off = await make_agent(sessions, owner, name="Off", memory_enabled=False)
    await say(rt, sessions, off, "hello there friend")
    assert memory_log.list_logs(off.id) == []
    thread = await rt.create_thread()
    await rt.send_turn(thread.id, "no agent")
    await finished(sessions, thread.id)


def test_the_log_refuses_links_and_stops_at_its_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    home = agent_memory.agent_home(1)
    (home / "memory").mkdir(parents=True)
    (home / "memory" / "log").symlink_to(elsewhere)
    assert memory_log.append(1, "x", source='chat "A"') is False and list(elsewhere.iterdir()) == []
    assert memory_log.list_logs(1) == []
    (home / "memory" / "log").unlink()
    assert memory_log.append(2, "first", source='chat "A"')
    monkeypatch.setattr(memory_log, "LOG_MAX_BYTES", 80)
    assert memory_log.append(2, "second line that is long enough to go over", source='chat "A"') is False
    assert memory_log.read_log(2, "../../x") is None and memory_log.read_log(2, "2026-13-45") is None


async def test_logs_can_be_read_and_deleted_and_go_with_the_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    assert memory_log.append(3, "something happened", source='chat "A"', now=datetime(2026, 10, 1, 9, 5))
    assert [d["day"] for d in memory_log.list_logs(3)] == ["2026-10-01"]
    assert "09:05 · from chat \"A\" · something happened" in memory_log.read_log(3, "2026-10-01")
    agent_memory.remove_memory(3)
    assert memory_log.list_logs(3) == []
    assert memory_log.append(3, "again", source='chat "A"', now=datetime(2026, 10, 2, 9, 5))
    assert memory_log.delete_log(3, "2026-10-02") and not memory_log.delete_log(3, "2026-10-02")


# --- the brief ------------------------------------------------------------------------------


async def test_the_brief_lists_the_other_conversations_newest_first_and_never_this_one(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner)
    await say(rt, sessions, agent, "Plan the Goa trip", title="Trip")
    await say(rt, sessions, agent, "Draft the invoice reply", title="Invoices")
    third = await say(rt, sessions, agent, "What is next?", title="Today")
    instructions = engine.contexts[-1].instructions
    assert instructions.count("Your recent work") == 1
    brief = instructions.split("Your recent work")[1]
    assert brief.index("Invoices") < brief.index("Trip")  # newest first
    assert "you said: \"Done: Draft the invoice reply" in brief and "Today" not in brief
    assert "1:1 chat" in brief and "hunter2" not in brief
    # the same conversation continued: its own earlier turn is not listed
    await say(rt, sessions, agent, "And after that?", thread=third)
    assert "What is next" not in engine.contexts[-1].instructions.split("Your recent work")[1]


async def test_the_brief_leaves_out_archived_old_failed_and_memory_off(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner)
    old = await say(rt, sessions, agent, "An old chat", title="Old")
    gone = await say(rt, sessions, agent, "An archived chat", title="Archived")
    failed_one = await rt.create_thread(agent_id=agent.id, title="Failed")
    engine.fail = True
    await rt.send_turn(failed_one.id, "this one fails")
    await finished(sessions, failed_one.id)
    engine.fail = False
    async with sessions() as db:
        await db.execute(update(Turn).where(Turn.thread_id == old.id).values(finished_at=datetime.now(UTC) - timedelta(hours=49)))
        await db.execute(update(Thread).where(Thread.id == gone.id).values(archived_at=datetime.now(UTC)))
        await db.commit()
    await say(rt, sessions, agent, "What now?", title="Now")
    instructions = engine.contexts[-1].instructions or ""
    assert "Your recent work" not in instructions
    off = await make_agent(sessions, owner, name="Off", memory_enabled=False)
    await say(rt, sessions, off, "hello there friend")
    assert "Your recent work" not in (engine.contexts[-1].instructions or "")


async def test_the_brief_is_capped(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner)
    for n in range(13):
        await say(rt, sessions, agent, f"chat number {n} " + "word " * 30, title=f"Topic {n}")
    await say(rt, sessions, agent, "latest", title="Latest")
    brief = engine.contexts[-1].instructions.split("Your recent work")[1]
    assert brief.count("\n- ") <= recent_work.MAX_LINES
    assert len(brief) <= recent_work.MAX_CHARS + len(recent_work.INTRO)


async def test_a_room_turn_gets_the_brief_and_the_private_chat_is_disclosed_once(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner, name="Lead")
    await say(rt, sessions, agent, "Secret plan with the person", title="Private")
    room = await rt.rooms.create({"name": "Standup", "member_ids": [agent.id], "default_responder_kind": "everyone"})
    await rt.rooms.post(room["id"], "Morning, what did you do?")
    await rt.rooms.work(room["id"])
    assert "Done: Secret plan with the person" in engine.contexts[-1].instructions
    notices = [m for m in await rt.rooms.messages(room["id"]) if m.get("kind") == "brief"]
    assert len(notices) == 1
    assert notices[0]["text"] == "Lead's recent-work brief covers 1 private chat with you"
    assert notices[0]["author"]["label"] == "Memory"
    await rt.rooms.post(room["id"], "And now?")
    await rt.rooms.work(room["id"])
    assert len([m for m in await rt.rooms.messages(room["id"]) if m.get("kind") == "brief"]) == 1


async def test_an_agents_own_file_edits_to_its_notes_are_journaled_and_undoable(harness, sessions, owner):
    rt, engine = harness
    agent = await make_agent(sessions, owner)
    agent_memory.write_memory(agent.id, "- 2026-10-01 · likes tea\n")
    memory_topics.write_topic(agent.id, "keep.md", "- unchanged\n")

    def edit(ctx):
        # What an agent with file tools does inside its own folder.
        (agent_memory.agent_home(agent.id) / "MEMORY.md").write_text("- 2026-10-01 · likes tea\n- 2026-10-23 · likes coffee too\n")
        (agent_memory.agent_home(agent.id) / "memory" / "new.md").write_text("- a new topic by the agent\n")

    engine.edit = edit
    await say(rt, sessions, agent, "Remember that I like coffee")
    rows = memory_journal.read(agent.id)
    assert {(r["path"], r["actor"], r["kind"]) for r in rows} == {("MEMORY.md", "agent", "edited"), ("memory/new.md", "agent", "created")}
    memory_journal.undo(agent.id, next(r["id"] for r in rows if r["path"] == "memory/new.md"))
    assert not memory_topics.read_topic(agent.id, "new.md").exists
    # an untouched file leaves no row, and a turn that edits nothing leaves none
    engine.edit = None
    before = len(memory_journal.read(agent.id))
    await say(rt, sessions, agent, "Thanks")
    assert len(memory_journal.read(agent.id)) == before
