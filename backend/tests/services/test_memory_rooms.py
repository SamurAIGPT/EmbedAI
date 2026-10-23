"""Learning from group chats: what a member agent read and answered in a room
feeds its capture, under room attribution rules (docs/MEMORY.md)."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services import (
    about_me,
    agent_memory,
    agent_store,
    local_claude_runner,
    memory_capture,
    memory_journal,
    memory_room,
    memory_settings,
)
from app.services.memory_capture import RoomLine, Turn
from app.services.provider_adapter import Capabilities, ProviderStatus, TurnOutcome
from app.services.thread_hub import ThreadHub
from app.services.thread_runtime import ThreadRuntime
from app.team import service
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

DAY = "2026-10-23"


class Engine:
    key = "claude"
    capabilities = Capabilities(tools=True, approvals=True, mcp=True)
    custom_models = True

    def __init__(self):
        self.answer = "[]"
        self.prompts: list[str] = []

    def new_session_id(self):
        return None

    async def status(self):
        return ProviderStatus(ready=True)

    async def run_turn(self, ctx, sink, gate):
        await sink.emit("assistant.message", {"text": "Noted, I will tell the team."})
        return TurnOutcome("succeeded", result="Noted, I will tell the team.", usage={"input_tokens": 3})

    async def generate_text(self, prompt, *, config=None, model=None, timeout=60):
        self.prompts.append(prompt)
        return self.answer


@pytest.fixture
async def harness(sessions, owner, monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path / "ws")
    monkeypatch.setattr(memory_journal, "state_root", lambda: tmp_path / "state")
    monkeypatch.setattr(settings, "TEAM_ENABLED", True)
    engine = Engine()
    rt = ThreadRuntime(hub=ThreadHub(), adapters={"claude": engine}, owner_id=owner, _session_factory=sessions)
    rt.capture._quiet = lambda: 0.1
    async with sessions() as db:
        lead = await agent_store.create_agent(db, owner_id=owner, name="Lead")
        worker = await agent_store.create_agent(db, owner_id=owner, name="Reviewer")
        await service.set_lead(db, owner, lead.id)
        await service.change_member(db, owner, worker.id)
    yield rt, engine, lead, worker
    await rt.shutdown()
    for task in rt.rooms.tasks.values():
        if not task.done():
            task.cancel()
    await asyncio.gather(*rt.rooms.tasks.values(), return_exceptions=True)


async def converse(rt, name, text, *, member, author=None, dispatch=True, others=()):
    """Create a room (Lead and Reviewer), post `others` then `text`, and let `member` answer."""
    room = await rt.rooms.create({"name": name, "member_ids": [member.id], "default_responder_kind": "member"})
    for other_author, other_text in others:
        await rt.rooms.post(room["id"], other_text, author=other_author, dispatch=False)
    await rt.rooms.post(room["id"], text, author=author, dispatch=dispatch)
    await rt.rooms.work(room["id"])
    await rt.capture.idle()
    await asyncio.sleep(0.3)
    await rt.capture.idle()
    return room


LAUNCH = "Lead, the garden launch moved to Friday, please tell everyone."


async def test_room_lines_are_attributed_to_the_person_another_person_or_another_agent():
    def event(seq, kind, text, **author):
        return SimpleNamespace(seq=seq, data={"author": {"kind": kind, **author}, "text": text})

    events = [
        event(1, "person", "I am vegetarian", label="You"),
        event(2, "agent", "Old reply", agent_id=7, label="Lead"),
        event(3, "person", "Budget is 5k", label="Priya", owner=False),
        event(4, "agent", "I checked the invoice", agent_id=9, label="Scout: x"),
        event(5, "person", "  ", label="You"),
        event(6, "agent", "My answer", agent_id=7, label="Lead"),
    ]
    lines = memory_room.lines_for(events, 7, events[-1])
    # only what came after the agent's own last message, in order
    assert lines == [RoomLine("other", "Priya", "Budget is 5k"), RoomLine("peer", "Scout x", "I checked the invoice")]
    assert memory_room.lines_for(events, 99, events[-1])[0] == RoomLine("person", "Person", "I am vegetarian")


def test_the_prompt_marks_each_voice_and_carries_the_room_rules():
    turn = Turn(
        1, "I am vegetarian", "Noted", owner=False,
        room=(RoomLine("person", "Person", "I am vegetarian"), RoomLine("other", "Priya", "Budget is 5k"),
              RoomLine("peer", "Scout", "I verified the invoice")),
    )
    text = memory_capture.prompt(agent_name="Lead", turns=[turn], notebook="", topics="", day=DAY)
    assert "Person: I am vegetarian\nPriya (another person): Budget is 5k\nScout (another agent): I verified the invoice\nLead: Noted" in text
    assert "group chat" in text and "never aboutUser" in text and "never what it says about the Person" in text
    plain = memory_capture.prompt(agent_name="Lead", turns=[Turn(1, "hi there friend", "ok")], notebook="", topics="", day=DAY)
    assert "group chat" not in plain


def test_a_room_batch_is_worth_a_call_by_every_line_it_read():
    short = Turn(1, "ok", "ok", room=(RoomLine("person", "Person", "ok"),))
    long = Turn(1, "", "ok", room=(RoomLine("other", "Priya", "The client wants the report on Friday"),))
    assert not memory_capture.worth_capturing([short]) and memory_capture.worth_capturing([long])


async def test_the_switch_is_off_by_default_and_a_room_feeds_nothing(harness):
    rt, engine, lead, _ = harness
    engine.answer = json.dumps([{"text": "The garden launch moved to Friday", "kind": "decision"}])
    await converse(rt, "Garden", LAUNCH, member=lead)
    assert engine.prompts == [] and "launch" not in agent_memory.read_memory(lead.id).text


async def test_with_the_switch_on_the_room_turn_is_filed_and_sourced_to_the_room(harness):
    rt, engine, lead, _ = harness
    memory_settings.update(learn_from_rooms=True)
    engine.answer = json.dumps([
        {"text": "The garden launch moved to Friday", "kind": "decision", "confidence": 0.9},
        {"text": "The person is vegetarian", "kind": "fact", "aboutUser": True},
    ])
    await converse(rt, "Garden", LAUNCH, member=lead)
    (prompt,) = engine.prompts
    assert "Person: " + LAUNCH in prompt and 'group chat ("room") Lead is a member of' in prompt
    notes = agent_memory.read_memory(lead.id).text
    assert 'from room "Garden" (noticed) · The garden launch moved to Friday' in notes
    assert about_me.read()["learned"][0]["text"] == "The person is vegetarian"  # the person's own line


async def test_another_persons_line_never_reaches_about_me_but_shared_work_is_kept(harness):
    rt, engine, lead, _ = harness
    memory_settings.update(learn_from_rooms=True)
    engine.answer = json.dumps([
        {"text": "The client budget is 5k", "kind": "fact"},
        {"text": "Priya is vegetarian", "kind": "fact", "aboutUser": True},
    ])
    others = [({"kind": "person", "label": "Priya", "owner": False}, "The client budget is 5k and I am vegetarian")]
    await converse(rt, "Client", LAUNCH, member=lead, others=others)
    assert "Priya (another person): The client budget is 5k" in engine.prompts[0]
    assert "The client budget is 5k" in agent_memory.read_memory(lead.id).text
    assert about_me.read()["learned"] == []


async def test_a_stretch_with_no_persons_words_is_skipped(harness):
    rt, engine, lead, worker = harness
    memory_settings.update(learn_from_rooms=True)
    engine.answer = json.dumps([{"text": "The agent said something", "kind": "fact"}])
    agent_author = {"kind": "agent", "agent_id": worker.id, "label": "Reviewer"}
    room = await converse(rt, "Agents", "@Lead I verified the invoice and it is correct.", member=lead, author=agent_author)
    answered = [m for m in await rt.rooms.messages(room["id"]) if m["author"].get("agent_id") == lead.id]
    assert answered  # the agent did answer in the room: only a person's words are missing
    assert engine.prompts == []


async def test_the_switch_is_checked_again_when_the_batch_is_captured(harness):
    rt, engine, lead, _ = harness
    memory_settings.update(learn_from_rooms=True)
    engine.answer = json.dumps([{"text": "The garden launch moved to Friday", "kind": "decision"}])
    rt.capture._quiet = lambda: 0.5
    room = await rt.rooms.create({"name": "Garden", "member_ids": [lead.id], "default_responder_kind": "member"})
    await rt.rooms.post(room["id"], LAUNCH)
    await rt.rooms.work(room["id"])
    memory_settings.update(learn_from_rooms=False)  # switched off while the batch waits
    await asyncio.sleep(0.8)
    await rt.capture.idle()
    assert engine.prompts == [] and "launch" not in agent_memory.read_memory(lead.id).text


async def test_a_waiting_room_turn_survives_a_restart_as_ids_only(harness):
    rt, engine, lead, _ = harness
    memory_settings.update(learn_from_rooms=True)
    engine.answer = json.dumps([{"text": "The garden launch moved to Friday", "kind": "decision"}])
    rt.capture._quiet = lambda: 30
    room = await rt.rooms.create({"name": "Garden", "member_ids": [lead.id], "default_responder_kind": "member"})
    await rt.rooms.post(room["id"], LAUNCH)
    await rt.rooms.work(room["id"])
    rt.capture.close()
    saved = json.loads(memory_capture._pending_path().read_text())
    assert saved["batches"][0]["agent_id"] == lead.id and "garden launch" not in json.dumps(saved)
    rt.capture.reopen()
    rt.capture._quiet = lambda: 0.1
    assert await rt.capture.restore() == 1
    await rt.capture.idle()
    assert 'from room "Garden" (noticed)' in agent_memory.read_memory(lead.id).text


async def test_upkeep_off_for_the_agent_means_the_room_is_not_read(harness, sessions):
    rt, engine, lead, _ = harness
    memory_settings.update(learn_from_rooms=True)
    async with sessions() as db:
        await agent_store.update_agent(db, lead.id, memory_upkeep=False)
    engine.answer = json.dumps([{"text": "The garden launch moved to Friday", "kind": "decision"}])
    await converse(rt, "Garden", LAUNCH, member=lead)
    assert engine.prompts == []
