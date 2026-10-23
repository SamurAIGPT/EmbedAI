"""Facts noticed in conversations: the parser, the filing, and the whole path
through a real runtime on the OpenAI-compatible engine (a mock endpoint)
(docs/MEMORY.md)."""

import asyncio
import json

import httpx
import pytest

from app.services import (
    agent_memory,
    agent_store,
    local_claude_runner,
    memory_capture,
    memory_entries,
    memory_journal,
    memory_topics,
)
from app.services.memory_capture import Fact
from tests.services.test_openai_chat_adapter import BASE, KEY, data_of, delta, endpoint, ort, run, sse  # noqa: F401
from tests.services.thread_harness import cleanup, events_of, finished, owner, sessions  # noqa: F401

DAY = "2026-10-23"


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: root)
    return root


# --- entry grammar ---------------------------------------------------------------------


def test_entries_read_date_until_and_struck():
    text = (
        "hand written\n"
        '- 2026-10-01 · from chat "A" (noticed) · Likes tea · until 2026-12-01\n'
        "  continued line\n"
        "- 2026-10-02 · ~~old fact~~ · superseded 2026-10-03\n"
        "- not dated\n"
    )
    entries = memory_entries.parse(text)
    assert [(e.day, e.until, e.struck, e.body) for e in entries] == [
        ("2026-10-01", "2026-12-01", False, "Likes tea"),
        ("2026-10-02", None, True, "old fact"),
    ]
    assert (entries[0].start, entries[0].end) == (1, 3)
    assert memory_entries.is_expired(entries[0], "2026-12-02")
    assert not memory_entries.is_expired(entries[0], "2026-12-01")
    assert not memory_entries.is_expired(entries[1], "2030-01-01")


def test_identity_is_narrow():
    same = memory_entries.identity
    assert same("- The person is vegetarian.") == same("The  person is vegetarian")
    assert same("Balance is -10") != same("Balance is 10")


# --- the parser ------------------------------------------------------------------------


def test_the_answer_is_read_from_fenced_or_bare_json():
    answer = '```json\n[{"text": "- The person lives in Pune", "kind": "fact", "confidence": 0.9}]\n```'
    (fact,) = memory_capture.parse_facts(answer, DAY)
    assert fact.text == "The person lives in Pune" and fact.topic is None and fact.until is None
    assert memory_capture.parse_facts("nothing worth keeping", DAY) == []
    assert memory_capture.parse_facts("[]", DAY) == []


def test_until_dates_are_checked_and_topics_named():
    answer = json.dumps([
        {"text": "Trip to Goa", "kind": "fact", "until": "2026-11-02", "topic": "Travel Plans", "topicAliases": ["trips", "a,b", "[x]"]},
        {"text": "Meeting was last week", "kind": "fact", "until": "2026-10-01"},  # past: dropped
        {"text": "Impossible day", "kind": "fact", "until": "2026-02-31"},  # not a day: dropped
        {"text": "Unsure", "kind": "fact", "confidence": 0.2},
        {"text": "Bad kind", "kind": "gossip"},
        {"text": "Reserved", "kind": "fact", "topic": "archive"},
    ])
    facts = memory_capture.parse_facts(answer, DAY)
    assert [f.text for f in facts] == ["Trip to Goa", "Reserved"]
    assert facts[0].topic == "travel-plans.md" and facts[0].until == "2026-11-02"
    assert facts[0].aliases == ("trips", "a b", "x")
    assert facts[1].topic is None  # a reserved name is never a topic


def test_trivial_exchanges_are_not_worth_a_call():
    turn = memory_capture.Turn
    assert not memory_capture.worth_capturing([turn(1, "reply with just: ok", "ok")])
    assert memory_capture.worth_capturing([turn(1, "I moved to Pune last month, for the new job", "ok")])
    assert memory_capture.worth_capturing([turn(1, "ok", "x" * 40)])


def test_helper_model_per_engine(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "MEMORY_HELPER_MODEL", "")
    assert memory_capture.helper_model("claude", "opus") == "haiku"
    assert memory_capture.helper_model("openai_compat", "llama-x") == "llama-x"
    monkeypatch.setattr(settings, "MEMORY_HELPER_MODEL", "claude=sonnet, openai_compat=tiny")
    assert memory_capture.helper_model("claude", None) == "sonnet"
    assert memory_capture.helper_model("openai_compat", "llama-x") == "tiny"


def test_the_prompt_has_what_the_call_needs():
    text = memory_capture.prompt(
        agent_name="Scout", turns=[memory_capture.Turn(1, "I am vegetarian", "Noted")],
        notebook="- 2026-10-01 · Likes tea", topics="- memory/travel.md — Travel", day=DAY,
    )
    assert "Friday, 2026-10-23" in text
    assert "Person: I am vegetarian" in text and "Scout: Noted" in text
    assert "memory/travel.md" in text and "Likes tea" in text and "until" in text


# --- filing ----------------------------------------------------------------------------


def test_core_facts_go_to_memory_marked_noticed_and_topics_get_headers(workspace_root):
    agent_memory.write_memory(1, "- hand written line\n")
    source = memory_entries.source_label('Plans · "x"', 7)
    added, topics = memory_capture.file_facts(
        1,
        [
            Fact("The person is vegetarian", "fact"),
            Fact("Trip to Goa from Friday", "fact", until="2026-11-02", topic="travel.md", aliases=("trips", "flights")),
            Fact("Prefers aisle seats", "preference", topic="travel.md"),
        ],
        source=source, day=DAY,
    )
    assert (added, topics) == (1, 2)
    notes = agent_memory.read_memory(1).text
    assert notes.startswith("- hand written line\n")
    assert '- 2026-10-23 · from chat "Plans \'x\'" (noticed) · The person is vegetarian' in notes
    note = memory_topics.read_topic(1, "travel.md").text
    assert note.startswith("---\ntitle: Travel\naliases: [trips, flights]\n---")
    assert "Trip to Goa from Friday · until 2026-11-02" in note and "Prefers aisle seats" in note
    # Facts ending on a day carry it in the grammar the loader and tidy-up read.
    assert [e.until for e in memory_entries.parse(note)] == ["2026-11-02", None]
    # Every file change is a journal row by "upkeep" that Undo reverses.
    rows = memory_journal.read(1)
    assert {(r["path"], r["actor"]) for r in rows} == {("MEMORY.md", "upkeep"), ("memory/travel.md", "upkeep")}
    row = next(r for r in rows if r["path"] == "memory/travel.md")
    memory_journal.undo(1, row["id"])
    assert not memory_topics.read_topic(1, "travel.md").exists


def test_an_existing_topic_is_reused_and_gains_new_aliases(workspace_root):
    memory_topics.write_topic(1, "Food.md", "---\ntitle: Food\naliases: [dinner]\n---\n- 2026-10-01 · Likes dal\n")
    memory_capture.file_facts(
        1, [Fact("Allergic to peanuts", "fact", topic="food.md", aliases=("dinner", "lunch", "allergy"))],
        source='chat "A"', day=DAY,
    )
    assert [t.name for t in memory_topics.list_topics(1)] == ["Food.md"]
    text = memory_topics.read_topic(1, "Food.md").text
    assert "aliases: [dinner, lunch, allergy]" in text
    assert text.count("Likes dal") == 1 and "Allergic to peanuts" in text


def test_nothing_already_known_or_secret_is_written(workspace_root):
    agent_memory.write_memory(1, "- 2026-10-01 · from chat \"A\" · The person lives in Pune\n")
    memory_topics.write_topic(1, "garden.md", "- Tomatoes go in the north bed\n")
    added, topics = memory_capture.file_facts(
        1,
        [
            Fact("The person lives in Pune.", "fact"),  # in MEMORY.md already
            Fact("The person lives in Pune", "fact", topic="garden.md"),  # same fact, wanted in a topic
            Fact("Tomatoes go in the north bed", "fact", topic="garden.md"),
            Fact("The password: hunter2hunter2", "fact"),
            Fact("Key is sk-ant-abcdefghijklmnop1234567890", "fact"),
        ],
        source='chat "A"', day=DAY,
    )
    assert (added, topics) == (0, 0)
    assert memory_journal.read(1) == []


def test_a_note_at_its_size_cap_takes_nothing_more(workspace_root):
    memory_topics.write_topic(1, "big.md", "- " + "x" * (memory_topics.TOPIC_MAX_BYTES - 10) + "\n")
    assert memory_capture.file_facts(1, [Fact("New", "fact", topic="big.md")], source='chat "A"', day=DAY) == (0, 0)


# --- through a runtime -------------------------------------------------------------------


def answering(endpoint, capture_answer):
    """Chat turns stream "Hello"; the quick background call (not streamed) gets `capture_answer`."""
    chat = endpoint.handler

    def handler(request):
        body = json.loads(request.content)
        if body.get("stream") is False:
            endpoint.calls.append(body)
            return httpx.Response(200, json={"choices": [{"message": {"content": capture_answer}}]})
        return chat(request)

    endpoint.calls = []
    endpoint.handler = handler


async def make_agent(sessions, owner, **kwargs):
    async with sessions() as db:
        return await agent_store.create_agent(db, owner_id=owner, name="cap-" + json.dumps(kwargs)[:20], provider="openai_compat", **kwargs)


LONG = "I moved to Pune last month and my sister Asha lives in Goa, please remember that"


async def test_a_quiet_conversation_is_read_once_and_facts_land(ort, sessions, owner, endpoint, workspace_root):
    answering(endpoint, json.dumps([
        {"text": "The person lives in Pune", "kind": "fact", "confidence": 0.9},
        {"text": "The person's sister Asha lives in Goa", "kind": "fact", "topic": "asha", "topicAliases": ["sister", "family"]},
    ]))
    ort.capture._quiet = lambda: 0.2
    agent = await make_agent(sessions, owner)
    thread, _ = await run(ort, sessions, LONG, agent_id=agent.id, title="Moving")
    assert endpoint.calls == []  # nothing yet: the conversation is not quiet
    await asyncio.sleep(0.5)
    await ort.capture.idle()
    (call,) = endpoint.calls
    prompt = call["messages"][0]["content"]
    assert "Person: " + LONG in prompt and call["model"] == "llama-x"
    notes = agent_memory.read_memory(agent.id).text
    assert 'from chat "Moving" (noticed) · The person lives in Pune' in notes
    asha = memory_topics.read_topic(agent.id, "asha.md").text
    assert "aliases: [sister, family]" in asha and "sister Asha lives in Goa" in asha
    status = memory_capture.read_status(agent.id)["last_capture"]
    assert (status["added"], status["topics"]) == (1, 1)
    # The key travelled only in the header of a call to the agent's own endpoint.
    assert KEY not in json.dumps(call)


async def test_six_turns_trigger_the_call_without_waiting(ort, sessions, owner, endpoint, workspace_root):
    answering(endpoint, "[]")
    agent = await make_agent(sessions, owner)
    thread = await ort.create_thread(provider="openai_compat", agent_id=agent.id)
    for n in range(1, 7):
        await run(ort, sessions, f"{LONG} {n}", count=n, thread=thread)
    await ort.capture.idle()
    assert len(endpoint.calls) == 1
    assert "Person: " + LONG + " 6" in endpoint.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("kwargs", [{"memory_upkeep": False}, {"memory_enabled": False}])
async def test_switches_off_means_no_call(ort, sessions, owner, endpoint, workspace_root, kwargs):
    answering(endpoint, '[{"text": "x fact here", "kind": "fact"}]')
    ort.capture._quiet = lambda: 0.1
    agent = await make_agent(sessions, owner, **kwargs)
    await run(ort, sessions, LONG, agent_id=agent.id)
    await asyncio.sleep(0.3)
    await ort.capture.idle()
    assert endpoint.calls == []


async def test_no_agent_no_call(ort, sessions, owner, endpoint, workspace_root):
    answering(endpoint, "[]")
    ort.capture._quiet = lambda: 0.1
    await run(ort, sessions, LONG)
    await asyncio.sleep(0.3)
    await ort.capture.idle()
    assert endpoint.calls == []


async def test_trivial_exchange_and_failed_turn_and_routine_run_are_never_read(ort, sessions, owner, endpoint, workspace_root):
    answering(endpoint, '[{"text": "x fact here", "kind": "fact"}]')
    ort.capture._quiet = lambda: 0.1
    agent = await make_agent(sessions, owner)
    # an exchange too short on both sides
    endpoint_chat = endpoint.handler
    await run(ort, sessions, "say ok", agent_id=agent.id)
    # a failed turn
    endpoint.handler = lambda r: httpx.Response(500, json={"error": {"message": "boom"}})
    thread = await ort.create_thread(provider="openai_compat", agent_id=agent.id)
    await ort.send_turn(thread.id, LONG)
    await finished(sessions, thread.id)
    endpoint.handler = endpoint_chat
    # a routine run (its turn is started for the routine, not the person)
    thread = await ort.create_thread(provider="openai_compat", agent_id=agent.id)
    await ort.send_turn(thread.id, LONG, team_origin="routine")
    await finished(sessions, thread.id)
    await asyncio.sleep(0.4)
    await ort.capture.idle()
    assert endpoint.calls == []


async def test_a_failing_helper_call_breaks_nothing(ort, sessions, owner, endpoint, workspace_root):
    chat = endpoint.handler
    endpoint.handler = lambda r: httpx.Response(500, json={}) if json.loads(r.content).get("stream") is False else chat(r)
    ort.capture._quiet = lambda: 0.1
    agent = await make_agent(sessions, owner)
    await run(ort, sessions, LONG, agent_id=agent.id)
    await asyncio.sleep(0.3)
    await ort.capture.idle()
    assert agent_memory.read_memory(agent.id).text == ""
    assert memory_capture.read_status(agent.id)["last_capture"]["note"]


async def test_one_call_per_agent_at_a_time(ort, sessions, owner, endpoint, workspace_root):
    running = {"now": 0, "max": 0}
    adapter = ort.adapters["openai_compat"]
    original = adapter.generate_text

    async def slow(*args, **kwargs):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.2)
        try:
            return await original(*args, **kwargs)
        finally:
            running["now"] -= 1

    adapter.generate_text = slow
    answering(endpoint, "[]")
    ort.capture._quiet = lambda: 0.05
    agent = await make_agent(sessions, owner)
    await asyncio.gather(
        run(ort, sessions, LONG + " a", agent_id=agent.id),
        run(ort, sessions, LONG + " b", agent_id=agent.id),
    )
    await asyncio.sleep(0.3)
    await ort.capture.idle()
    assert len(endpoint.calls) == 2 and running["max"] == 1


async def test_a_deleted_conversation_leaves_nothing_to_read(ort, sessions, owner, endpoint, workspace_root):
    answering(endpoint, '[{"text": "x fact here", "kind": "fact"}]')
    ort.capture._quiet = lambda: 0.3
    agent = await make_agent(sessions, owner)
    thread, _ = await run(ort, sessions, LONG, agent_id=agent.id)
    from sqlalchemy import delete

    from app.db.models import Event, Thread, Turn

    async with sessions() as db:
        await db.execute(delete(Event).where(Event.thread_id == thread.id))
        await db.execute(delete(Turn).where(Turn.thread_id == thread.id))
        await db.execute(delete(Thread).where(Thread.id == thread.id))
        await db.commit()
    await asyncio.sleep(0.6)
    await ort.capture.idle()
    assert endpoint.calls == []


async def test_turns_waiting_at_shutdown_are_read_back_at_the_next_start(ort, sessions, owner, endpoint, workspace_root):
    answering(endpoint, '[{"text": "The person lives in Pune", "kind": "fact"}]')
    ort.capture._quiet = lambda: 30
    agent = await make_agent(sessions, owner)
    await run(ort, sessions, LONG, agent_id=agent.id, title="Moving")
    ort.capture.close()  # the app quits
    saved = json.loads(memory_capture._pending_path().read_text())
    assert saved["batches"][0]["agent_id"] == agent.id and "Pune" not in json.dumps(saved)  # ids, never text
    ort.capture.reopen()
    assert await ort.capture.restore() == 1
    await ort.capture.idle()
    assert "The person lives in Pune" in agent_memory.read_memory(agent.id).text
    assert not memory_capture._pending_path().exists()


async def test_the_claude_call_runs_the_cli_with_nothing_but_text(tmp_path, monkeypatch):
    import sys

    from app.services.claude_adapter import ClaudeAdapter

    fake = tmp_path / "claude"
    fake.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'stdin': sys.stdin.read(), 'cwd_files': os.listdir('.')}))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setattr(local_claude_runner, "resolve_claude_executable", lambda: fake)
    out = json.loads(await ClaudeAdapter().generate_text("the prompt", model="haiku"))
    args = out["args"]
    assert out["stdin"] == "the prompt" and "the prompt" not in args  # on stdin, never on the command line
    assert args[args.index("--tools") + 1] == "" and "--no-session-persistence" in args
    assert "--strict-mcp-config" in args and args[args.index("--model") + 1] == "haiku"
    assert out["cwd_files"] == []  # started in a new empty folder


async def test_a_claude_call_that_fails_raises(tmp_path, monkeypatch):
    from app.services.claude_adapter import ClaudeAdapter

    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 3\n")
    fake.chmod(0o755)
    monkeypatch.setattr(local_claude_runner, "resolve_claude_executable", lambda: fake)
    with pytest.raises(RuntimeError):
        await ClaudeAdapter().generate_text("x")
