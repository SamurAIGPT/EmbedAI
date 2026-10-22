"""Topic notes, the journal, proposed notes and recall over HTTP, with a real
child process playing the Claude CLI (docs/MEMORY.md)."""

from app.auth import policy
from app.services import memory_proposals, memory_recall, memory_topics
from tests.routers.test_agents_api import (  # noqa: F401
    agent_cleanup,
    api,
    code_of,
    launches,
    local_enabled,
    make,
    thread_from,
)
from tests.services.thread_harness import cleanup, cli, events_of, finished, owner, rt, sessions  # noqa: F401


def topic_url(agent, name="travel.md"):
    return f"/api/v1/agents/{agent['id']}/memory/topics/{name}"


async def test_a_topic_note_is_created_listed_read_and_deleted(api):
    agent = await make(api)
    empty = (await api.get(topic_url(agent))).json()
    assert empty["exists"] is False and empty["text"] == ""
    saved = await api.put(
        topic_url(agent), json={"text": "# Trips\n- aisle\n- token=abcdef123456\n", "expected_hash": empty["hash"]}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["redacted"] == 1 and "abcdef123456" not in saved.json()["text"]
    listed = (await api.get(f"/api/v1/agents/{agent['id']}/memory/topics")).json()
    assert [(t["name"], t["title"]) for t in listed["topics"]] == [("travel.md", "Trips")]
    assert listed["max_topics"] == memory_topics.MAX_TOPICS and listed["folder"].endswith("/memory")
    assert (await api.delete(topic_url(agent))).status_code == 204
    assert (await api.delete(topic_url(agent))).status_code == 404


async def test_a_stale_save_is_409_and_bad_names_are_422(api):
    agent = await make(api)
    first = (await api.put(topic_url(agent), json={"text": "- one\n"})).json()
    memory_topics.write_topic(agent["id"], "travel.md", "- the agent wrote this\n")
    stale = await api.put(topic_url(agent), json={"text": "- mine\n", "expected_hash": first["hash"]})
    assert stale.status_code == 409 and code_of(stale) == "memory_changed"
    assert stale.json()["detail"]["current"] == "- the agent wrote this\n"
    for bad in ("MEMORY.md", ".hidden.md", "x.txt", "a.b.md", "travel.md%0A"):
        response = await api.put(topic_url(agent, bad), json={"text": "x"})
        assert response.status_code == 422, bad
    assert (await api.put(topic_url(agent, "big.md"), json={"text": "x" * 70_000})).status_code == 413


async def test_the_journal_records_editor_saves_and_undo_restores(api):
    agent = await make(api)
    await api.put(f"/api/v1/agents/{agent['id']}/memory", json={"text": "- one\n"})
    await api.put(topic_url(agent), json={"text": "- Lisbon\n"})
    await api.put(topic_url(agent), json={"text": "- Porto\n"})
    rows = (await api.get(f"/api/v1/agents/{agent['id']}/memory/journal")).json()
    assert [(r["path"], r["kind"], r["actor"]) for r in rows] == [
        ("memory/travel.md", "edited", "person"),
        ("memory/travel.md", "created", "person"),
        ("MEMORY.md", "created", "person"),
    ]
    undone = await api.post(f"/api/v1/agents/{agent['id']}/memory/journal/{rows[0]['id']}/undo")
    assert undone.status_code == 200 and undone.json()["actor"] == "undo"
    assert (await api.get(topic_url(agent))).json()["text"] == "- Lisbon\n"
    assert (await api.post(f"/api/v1/agents/{agent['id']}/memory/journal/nope/undo")).status_code == 404


async def test_proposals_from_a_quiet_conversation_wait_for_accept_or_reject(api, rt, sessions, monkeypatch, launches):
    monkeypatch.setattr(memory_proposals, "QUIET_SECONDS", 0)
    agent = await make(api)
    thread = (await thread_from(api, agent)).json()
    await rt.send_turn(thread["id"], "Please remember that my train leaves at 8. I prefer window seats.")
    await finished(sessions, thread["id"], 1)
    url = f"/api/v1/agents/{agent['id']}/memory/proposals"
    proposals = (await api.get(url)).json()
    assert [p["text"] for p in proposals] == ["my train leaves at 8", "I prefer window seats"]
    assert (await api.get(f"/api/v1/agents/{agent['id']}/memory")).json()["text"] == ""  # nothing written
    accepted = await api.post(f"{url}/{proposals[0]['id']}/accept", json={})
    assert accepted.status_code == 200 and accepted.json()["path"] == "MEMORY.md"
    assert (await api.post(f"{url}/{proposals[1]['id']}/reject")).status_code == 204
    assert (await api.get(url)).json() == []  # read once; rejected never comes back
    notes = (await api.get(f"/api/v1/agents/{agent['id']}/memory")).json()["text"]
    assert 'The person said: "my train leaves at 8"' in notes
    assert (await api.post(f"{url}/{proposals[0]['id']}/accept", json={})).status_code == 404


async def test_recall_reaches_the_claude_turn_and_is_stored_as_an_event(api, rt, sessions, cli, launches):
    agent = await make(api)
    first = (await thread_from(api, agent)).json()
    await rt.send_turn(first["id"], "The spare house key is under the blue flowerpot")
    await finished(sessions, first["id"], 1)
    await api.put(topic_url(agent, "house.md"), json={"text": "- spare key moved in October\n"})
    second = (await thread_from(api, agent)).json()
    await rt.send_turn(second["id"], "Where is the spare house key?")
    await finished(sessions, second["id"], 1)
    prompt = cli.log("prompt")[-1]["prompt"]
    assert prompt.startswith(memory_recall.OPEN) and prompt.endswith("Where is the spare house key?")
    assert "blue flowerpot" in prompt and "memory/house.md" in prompt
    events = await events_of(sessions, second["id"])
    recalled = [e.data for e in events if e.type == "memory.recalled"]
    assert len(recalled) == 1 and recalled[0]["notes"][0]["name"] == "house.md"
    assert [e.data for e in events if e.type == "turn.started"][0]["prompt_text"] == "Where is the spare house key?"

    await api.patch(f"/api/v1/agents/{agent['id']}", json={"recall_enabled": False})
    await rt.send_turn(second["id"], "Where is the spare house key again?")
    await finished(sessions, second["id"], 2)
    assert cli.log("prompt")[-1]["prompt"] == "Where is the spare house key again?"


async def test_recall_switch_is_on_by_default_and_copied(api):
    agent = await make(api)
    assert agent["recall_enabled"] is True
    off = await make(api, recall_enabled=False)
    copy = (await api.post(f"/api/v1/agents/{off['id']}/duplicate")).json()
    assert copy["recall_enabled"] is False
    assert (await api.patch(f"/api/v1/agents/{agent['id']}", json={"recall_enabled": None})).status_code == 422


def test_memory_routes_are_admin_only():
    for method, path in (
        ("GET", "/api/v1/agents/1/memory/topics"),
        ("PUT", "/api/v1/agents/1/memory/topics/a.md"),
        ("GET", "/api/v1/agents/1/memory/journal"),
        ("POST", "/api/v1/agents/1/memory/journal/x/undo"),
        ("GET", "/api/v1/agents/1/memory/proposals"),
        ("POST", "/api/v1/agents/1/memory/proposals/x/accept"),
    ):
        assert policy.required_scope(method, path) == "admin", path
        assert not policy.is_public(method, path)
