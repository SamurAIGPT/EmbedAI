"""Organizing and the nightly tidy-up through a real runtime on the
OpenAI-compatible engine (a mock endpoint) (docs/MEMORY.md)."""

import json
from datetime import datetime

import httpx
import pytest

from app.core.config import settings
from app.services import agent_memory, agent_store, local_claude_runner, memory_capture, memory_journal, memory_topics
from tests.services.test_openai_chat_adapter import BASE, KEY, endpoint, ort  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_CAPTURE_QUIET_SECONDS", 0)


def line(day, text, extra=""):
    return f'- {day} · from chat "A" · {text}{extra}'


def script(endpoint, organize=None, pairs=None):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        seen.append(prompt)
        if "core notebook" in prompt:
            answer = organize or {"moves": []}
        else:
            answer = pairs or {"pairs": []}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    endpoint.handler = handler
    return seen


async def agent(sessions, owner, **kw):
    async with sessions() as db:
        return await agent_store.create_agent(db, owner_id=owner, name="Scout", provider="openai_compat", **kw)


NOTES = "\n".join([
    "# My notes (hand written)",
    line("2026-01-01", "The person lives in Pune"),
    line("2026-02-01", "The person lives in Mumbai"),
    line("2026-01-02", "Asha's school is Greenfield"),
    line("2026-01-03", "The person is vegetarian"),
    line("2026-01-04", "Likes tea"),
    line("2026-03-01", "Likes tea."),
    line("2026-01-05", "Dentist on Monday", " · until 2026-02-01"),
    line("2026-04-01", "Plain filler one"),
    line("2026-04-02", "Plain filler two"),
]) + "\n"


async def test_the_tidy_up_organizes_expires_dedupes_and_strikes(ort, sessions, owner, endpoint, tmp_path, monkeypatch):
    a = await agent(sessions, owner)
    agent_memory.write_memory(a.id, NOTES)
    seen = script(
        endpoint,
        organize={"moves": [{"i": 2, "topic": "Asha", "aliases": ["sister"]}]},
        pairs={"pairs": [{"a": 0, "b": 1, "keep": "b", "remainder": "The person likes quiet"}]},
    )
    # the organize prompt never offers a health or hand-written line
    report = await ort.capture.tidy(a.id)
    organize_prompt = seen[0]
    assert "vegetarian" not in organize_prompt and "hand written" not in organize_prompt and "Dentist" not in organize_prompt
    notes = agent_memory.read_memory(a.id).text
    assert notes.startswith("# My notes (hand written)\n") and "vegetarian" in notes
    assert "Greenfield" not in notes and "Greenfield" in memory_topics.read_topic(a.id, "asha.md").text
    assert "aliases: [sister]" in memory_topics.read_topic(a.id, "asha.md").text
    # struck, never deleted, with the still-true part kept as its own entry
    today = datetime.now().strftime("%Y-%m-%d")
    assert f"~~The person lives in Pune~~ · superseded {today}" in notes
    assert f"from tidy-up · The person likes quiet" in notes
    assert notes.count("Likes tea") == 1 and "Dentist" not in notes
    archive = memory_topics.read_topic(a.id, "archive.md").text
    assert f"Dentist on Monday · until 2026-02-01 · expired {today}" in archive
    assert (report["organized"], report["expired"], report["duplicates"], report["superseded"]) == (1, 1, 1, 1)
    assert memory_capture.read_status(a.id)["last_tidy"]["superseded"] == 1
    # journaled by upkeep: the archive row comes before the MEMORY.md row that removed the line
    rows = [(r["path"], r["actor"]) for r in reversed(memory_journal.read(a.id))]
    assert all(actor == "upkeep" for _, actor in rows)
    assert rows.index(("memory/archive.md", "upkeep")) < len(rows) - 1 - rows[::-1].index(("MEMORY.md", "upkeep"))
    # a second run (the model now finds nothing) does nothing new
    script(endpoint)
    again = await ort.capture.tidy(a.id)
    assert (again["expired"], again["duplicates"], again["superseded"]) == (0, 0, 0)


async def test_an_engine_without_a_quick_call_still_expires_and_dedupes(ort, sessions, owner, monkeypatch):
    a = await agent(sessions, owner)
    agent_memory.write_memory(a.id, NOTES)
    monkeypatch.delattr(type(ort.adapters["openai_compat"]), "generate_text")
    report = await ort.capture.tidy(a.id)
    assert report["expired"] == 1 and report["duplicates"] == 1 and report["superseded"] == 0
    assert "cannot make the quick call" in report["note"]


async def test_stale_answers_are_dropped_when_the_notes_change_meanwhile(ort, sessions, owner, endpoint):
    a = await agent(sessions, owner)
    agent_memory.write_memory(a.id, NOTES)
    base = script(endpoint, pairs={"pairs": [{"a": 0, "b": 1, "keep": "b"}]})
    handler = endpoint.handler

    def changing(request):
        agent_memory.write_memory(a.id, agent_memory.read_memory(a.id).text + "- the agent wrote this meanwhile\n")
        return handler(request)

    endpoint.handler = changing
    report = await ort.capture.tidy(a.id)
    notes = agent_memory.read_memory(a.id).text
    assert "~~" not in notes and "meanwhile" in notes
    assert report["note"] and "changed" in report["note"]


async def test_switches_off_and_busy_agents_are_left_alone(ort, sessions, owner, endpoint):
    off = await agent(sessions, owner, memory_upkeep=False)
    agent_memory.write_memory(off.id, NOTES)
    script(endpoint)
    assert "off" in (await ort.capture.tidy(off.id))["note"]
    assert agent_memory.read_memory(off.id).text == NOTES


def test_due_after_the_hour_once_a_day(monkeypatch, tmp_path):
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: tmp_path)
    cap = memory_capture.MemoryCapture(None)
    monkeypatch.setattr(settings, "MEMORY_TIDY_HOUR", 3)
    assert not cap.due(1, datetime(2026, 10, 23, 2, 59))
    assert cap.due(1, datetime(2026, 10, 23, 3, 0))
    memory_capture.memory_status.update(1, last_tidy={"at": datetime(2026, 10, 23, 3, 5).timestamp()})
    assert not cap.due(1, datetime(2026, 10, 23, 23, 0))
    assert cap.due(1, datetime(2026, 10, 24, 3, 1))
    monkeypatch.setattr(settings, "MEMORY_TIDY_HOUR", 24)
    assert not cap.due(1, datetime(2026, 10, 25, 12, 0))
