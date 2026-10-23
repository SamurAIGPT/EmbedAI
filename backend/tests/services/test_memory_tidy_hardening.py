"""Rules the tidy-up keeps whatever the model says, and what it does when a
write cannot happen (docs/MEMORY.md)."""

import json
from datetime import datetime

import httpx
import pytest

from app.core.config import settings
from app.services import (
    agent_memory,
    agent_store,
    local_claude_runner,
    memory_capture,
    memory_entries,
    memory_journal,
    memory_settings,
    memory_tidy,
    memory_topics,
)
from tests.services.test_openai_chat_adapter import endpoint, ort  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

TODAY = "2026-10-23"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_CAPTURE_QUIET_SECONDS", 0)
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: root)
    monkeypatch.setattr(memory_journal, "state_root", lambda: tmp_path / "state")


def test_a_health_fact_is_never_archived_or_struck():
    text = (
        "- 2026-09-11 · On medication · until 2026-10-05\n"
        "- 2026-10-01 · The person is allergic to nuts\n"
        "- 2026-10-02 · The person is not allergic to nuts\n"
        "- 2026-09-10 · Trip · until 2026-10-05\n"
    )
    chosen = memory_tidy.plan(text, TODAY, [memory_tidy.Pair(0, 1, "b")], minimum_budget=1)
    assert [e.body for e in chosen.expired] == ["Trip"] and chosen.superseded == []


def test_a_struck_entry_keeps_its_continuation_lines_and_the_remainder_follows_them():
    text = "- 2026-09-01 · Lives in Pune and likes tea\n  (said in passing)\n- 2026-10-01 · Lives in Goa\n"
    chosen = memory_tidy.plan(text, TODAY, [memory_tidy.Pair(0, 1, "b", "Likes tea")], minimum_budget=1)
    out, _ = memory_tidy.apply(text, chosen, TODAY)
    assert out.split("\n")[:3] == [
        "- 2026-09-01 · ~~Lives in Pune and likes tea~~ · superseded 2026-10-23",
        "  (said in passing)",
        "- 2026-10-23 · from tidy-up · Likes tea",
    ]


def test_the_prompts_never_carry_a_secret():
    entries = memory_entries.parse("- 2026-10-01 · The api_key: abcdefghijkl123456 is for staging\n")
    for prompt in (memory_tidy.contradiction_prompt(entries), memory_tidy.organize_prompt(entries, "")):
        assert "abcdefghijkl123456" not in prompt


def script(endpoint, answers):
    def handler(request):
        prompt = json.loads(request.content)["messages"][0]["content"]
        answer = answers["organize" if "core notebook" in prompt else "pairs"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    endpoint.handler = handler


async def make(sessions, owner):
    async with sessions() as db:
        return await agent_store.create_agent(db, owner_id=owner, name="Scout", provider="openai_compat")


NOTES = (
    "Hand written\n"
    "- 2026-09-10 · Dentist · until 2026-10-05\n"
    "  at 9\n"
    "- 2026-10-03 · Asha lives in Delhi\n"
    "  (she moved in May)\n"
    "- 2026-10-04 · Likes tea\n"
)


async def test_organizing_moves_an_entry_with_its_continuation_lines(ort, sessions, owner, endpoint):
    agent = await make(sessions, owner)
    agent_memory.write_memory(agent.id, NOTES)
    script(endpoint, {"organize": {"moves": [{"i": 0, "topic": "asha", "aliases": ["sister"]}]}, "pairs": {"pairs": []}})
    assert await ort.capture.organize(agent.id) == 1
    assert "Asha" not in agent_memory.read_memory(agent.id).text
    assert "Asha lives in Delhi\n  (she moved in May)" in memory_topics.read_topic(agent.id, "asha.md").text


async def test_a_full_archive_leaves_expired_entries_where_they_are(ort, sessions, owner, endpoint):
    agent = await make(sessions, owner)
    agent_memory.write_memory(agent.id, NOTES + "- 2026-10-04 · Likes tea\n")
    memory_topics.write_topic(agent.id, "archive.md", "- " + "x" * (memory_topics.TOPIC_MAX_BYTES - 10) + "\n")
    script(endpoint, {"organize": {"moves": []}, "pairs": {"pairs": []}})
    report = await ort.capture.tidy(agent.id)
    assert report["expired"] == 0 and report["duplicates"] == 1
    assert "Dentist" in agent_memory.read_memory(agent.id).text


async def test_the_archive_is_put_back_when_the_notes_cannot_be_written(ort, sessions, owner, endpoint, monkeypatch):
    agent = await make(sessions, owner)
    agent_memory.write_memory(agent.id, NOTES)
    memory_topics.write_topic(agent.id, "archive.md", "- 2026-01-01 · Old\n")
    script(endpoint, {"organize": {"moves": []}, "pairs": {"pairs": []}})
    real = agent_memory.write_memory

    def conflicting(agent_id, text, *, expected_hash=None):
        if expected_hash is not None:  # someone edited the notes after the plan was made
            raise agent_memory.MemoryConflict(agent_memory.read_memory(agent_id))
        return real(agent_id, text)

    monkeypatch.setattr(agent_memory, "write_memory", conflicting)
    before_rows = len(memory_journal.read(agent.id))
    report = await ort.capture.tidy(agent.id)
    assert report["expired"] == 0
    assert memory_topics.read_topic(agent.id, "archive.md").text == "- 2026-01-01 · Old\n"
    assert agent_memory.read_memory(agent.id).text == NOTES
    assert len(memory_journal.read(agent.id)) == before_rows


def test_the_tidy_hour_and_helper_model_can_be_set(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_TIDY_HOUR", 3)
    cap = memory_capture.MemoryCapture(None)
    memory_settings.update(tidy_hour=6, helper_models={"claude": "sonnet"})
    assert not cap.due(1, datetime(2026, 10, 23, 5, 0)) and cap.due(1, datetime(2026, 10, 23, 6, 0))
    assert memory_capture.helper_model("claude", None) == "sonnet"
    assert memory_capture.helper_model("openai_compat", "own") == "own"
    memory_settings.update(tidy_hour=24)
    assert not cap.due(1, datetime(2026, 10, 23, 23, 0))
    with pytest.raises(memory_settings.InvalidSetting):
        memory_settings.update(tidy_hour=25)
