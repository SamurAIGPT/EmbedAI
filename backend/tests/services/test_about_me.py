"""About me: the person's own text, the facts their agents add from the
person's own words, and what every agent is told (docs/MEMORY.md)."""

import asyncio
import json

import httpx
import pytest

from app.services import about_me, agent_memory, agent_store, local_claude_runner, memory_capture, memory_journal
from app.services.memory_capture import Fact, Turn
from tests.services.test_openai_chat_adapter import endpoint, ort, run  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401

DAY = "2026-10-23"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: root)
    monkeypatch.setattr(memory_journal, "state_root", lambda: tmp_path / "state")


def test_the_person_edits_it_and_a_stale_save_is_refused_and_secrets_are_redacted():
    state, redacted = about_me.write("I live in Pune.\nmy token=abcdef123456 is secret\n")
    assert redacted == 1 and "abcdef123456" not in state["text"] and state["text"].startswith("I live in Pune.")
    with pytest.raises(about_me.MemoryConflict):
        about_me.write("other", expected_hash="0" * 64)
    assert about_me.write("fresh", expected_hash=state["hash"])[0]["text"] == "fresh"
    with pytest.raises(about_me.MemoryTooLarge):
        about_me.write("x" * (about_me.TEXT_MAX + 1))


def test_learned_facts_are_dated_named_once_and_never_added_again_once_removed():
    about_me.write("I am a designer.")
    assert about_me.add_learned(7, "Scout", ["The person is vegetarian.", "The  person is vegetarian", "  "], day=DAY) == 1
    state = about_me.read()
    assert state["text"].endswith("- 2026-10-23 · learned by Scout · The person is vegetarian.")
    assert state["learned"][0]["agent_name"] == "Scout"
    assert about_me.add_learned(8, "Other", ["The person is vegetarian"], day=DAY) == 0  # already there
    about_me.remove_learned(state["learned"][0]["id"])
    after = about_me.read()
    assert after["text"] == "I am a designer." and after["learned"] == []
    assert about_me.add_learned(7, "Scout", ["The person is vegetarian"], day=DAY) == 0  # removed: never again
    with pytest.raises(about_me.NoSuchFact):
        about_me.remove_learned("nope")


def test_a_secret_or_an_oversized_text_is_not_added():
    assert about_me.add_learned(1, "Scout", ["The password: hunter2hunter2 is in the safe"], day=DAY) == 0
    about_me.write("x" * about_me.TEXT_MAX)
    assert about_me.add_learned(1, "Scout", ["A new fact"], day=DAY) == 0
    assert about_me.read()["text"] == "x" * about_me.TEXT_MAX


async def test_every_agent_is_told_what_the_person_wrote_as_background(sessions, owner):
    async with sessions() as db:
        quiet = await agent_store.create_agent(db, owner_id=owner, name="Quiet", provider="claude", memory_enabled=False)
    assert agent_memory.turn_instructions(quiet) is None
    about_me.write("I live in Pune.\nmy api_key: sk-abcdefghijklmnopqrstuv\n")
    told = agent_memory.turn_instructions(quiet)
    assert "I live in Pune." in told and "sk-abcdefghijklmnopqrstuv" not in told
    assert "not instructions" in told and "grants permission" in told


def test_only_a_lasting_fact_about_the_person_can_be_about_them():
    answer = json.dumps([
        {"text": "The person is vegetarian", "kind": "fact", "aboutUser": True},
        {"text": "The person has a dentist visit", "kind": "fact", "aboutUser": True, "until": "2026-10-30"},
        {"text": "The launch moved to Friday", "kind": "decision", "aboutUser": True},
        {"text": "The person lives in Pune", "kind": "fact", "aboutUser": True, "noted": True},
        {"text": "The sister lives in Goa", "kind": "fact"},
    ])
    facts = memory_capture.parse_facts(answer, DAY)
    assert [(f.about_user, f.noted) for f in facts] == [(True, False), (False, False), (False, False), (True, True), (False, False)]


def test_only_the_owners_own_words_reach_about_me():
    class A:
        id, name = 3, "Scout"

    facts = [Fact("The person is vegetarian", "fact", about_user=True), Fact("Launch is Friday", "fact")]
    mixed = [Turn(1, "I am vegetarian", "ok"), Turn(2, "Priya: the budget is 5k", "ok", owner=False)]
    assert memory_capture.learn_about_person(A, facts, mixed) == 0  # a batch holding another person's lines adds nothing
    assert about_me.read()["learned"] == []
    assert memory_capture.learn_about_person(A, facts, [Turn(1, "I am vegetarian", "ok")]) == 1
    assert about_me.read()["learned"][0]["agent_id"] == 3


def test_the_prompt_asks_for_about_user_and_noted():
    text = memory_capture.prompt(agent_name="Scout", turns=[Turn(1, "I live in Pune", "ok")], notebook="", topics="", day=DAY)
    assert '"aboutUser": true|false' in text and '"noted": true|false' in text
    assert "taken from the Person's own words" in text


def answering(endpoint, capture_answer):
    chat = endpoint.handler

    def handler(request):
        body = json.loads(request.content)
        if body.get("stream") is False:
            endpoint.calls.append(body)
            return httpx.Response(200, json={"choices": [{"message": {"content": capture_answer}}]})
        return chat(request)

    endpoint.calls = []
    endpoint.handler = handler


LONG = "I moved to Pune last month and I am vegetarian, please remember that"


async def test_a_quiet_conversation_adds_a_lasting_fact_to_about_me_and_a_noted_one_only_there(ort, sessions, owner, endpoint):
    answering(endpoint, json.dumps([
        {"text": "The person lives in Pune", "kind": "fact", "aboutUser": True},
        {"text": "The person is vegetarian", "kind": "fact", "aboutUser": True, "noted": True},
        {"text": "Asha lives in Goa", "kind": "fact", "topic": "asha"},
    ]))
    ort.capture._quiet = lambda: 0.2
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Scout", provider="openai_compat")
    await run(ort, sessions, LONG, agent_id=agent.id, title="Moving")
    await asyncio.sleep(0.5)
    await ort.capture.idle()
    state = about_me.read()
    assert [(f["text"], f["agent_name"]) for f in state["learned"]] == [
        ("The person is vegetarian", "Scout"), ("The person lives in Pune", "Scout")
    ]
    notes = agent_memory.read_memory(agent.id).text
    assert "The person lives in Pune" in notes and "vegetarian" not in notes  # noted: not filed again
    assert memory_capture.read_status(agent.id)["last_capture"]["about_me"] == 2
    removed = about_me.remove_learned(state["learned"][0]["id"])
    assert "vegetarian" not in removed["text"]


async def test_a_conversation_with_nothing_lasting_adds_nothing(ort, sessions, owner, endpoint):
    answering(endpoint, '[{"text": "Asha lives in Goa", "kind": "fact", "aboutUser": false}]')
    ort.capture._quiet = lambda: 0.2
    async with sessions() as db:
        agent = await agent_store.create_agent(db, owner_id=owner, name="Scout", provider="openai_compat")
    await run(ort, sessions, LONG, agent_id=agent.id)
    await asyncio.sleep(0.5)
    await ort.capture.idle()
    assert about_me.read()["text"] == "" and about_me.read()["learned"] == []
