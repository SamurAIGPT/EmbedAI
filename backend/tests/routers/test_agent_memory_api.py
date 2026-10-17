"""An agent's notes, starter notes, and export/import, over HTTP; and what a
turn of a conversation with the agent is told."""

from uuid import uuid4

from app.services import agent_memory, agent_package
from tests.routers.test_agents_api import (  # noqa: F401
    agent_cleanup,
    api,
    code_of,
    launches,
    local_enabled,
    make,
    send,
    thread_from,
)
from tests.services.thread_harness import cleanup, cli, finished, owner, rt, sessions  # noqa: F401


async def memory_of(api, agent):
    response = await api.get(f"/api/v1/agents/{agent['id']}/memory")
    assert response.status_code == 200, response.text
    return response.json()


# --- reading and writing the notes ----------------------------------------------------


async def test_a_new_agent_has_empty_notes_in_its_own_folder(api):
    agent = await make(api)
    assert agent["memory_enabled"] is True
    memory = await memory_of(api, agent)
    assert memory["text"] == "" and memory["lines"] == 0 and memory["enabled"] is True
    assert memory["path"].endswith(f"agents/{agent['id']}/MEMORY.md")
    assert (memory["max_lines"], memory["max_bytes"]) == (agent_memory.MAX_LINES, agent_memory.MAX_BYTES)


async def test_starter_notes_become_the_agents_first_notes(api):
    agent = await make(api, starter_notes="- Uses pnpm\n- password=supersecret1\n")
    memory = await memory_of(api, agent)
    assert memory["text"] == "- Uses pnpm\n- password=[redacted]\n"
    assert "starter_notes" not in agent


async def test_notes_are_saved_with_the_hash_that_was_read(api):
    agent = await make(api)
    first = await memory_of(api, agent)
    saved = await api.put(
        f"/api/v1/agents/{agent['id']}/memory", json={"text": "- one\n", "expected_hash": first["hash"]}
    )
    assert saved.status_code == 200 and saved.json()["text"] == "- one\n" and saved.json()["redacted"] == 0
    assert (await memory_of(api, agent))["hash"] == saved.json()["hash"]


async def test_a_save_over_a_change_made_meanwhile_is_refused(api):
    agent = await make(api)
    first = await memory_of(api, agent)
    agent_memory.write_memory(agent["id"], "- the agent wrote this\n")  # e.g. with its file tools
    stale = await api.put(
        f"/api/v1/agents/{agent['id']}/memory", json={"text": "- mine\n", "expected_hash": first["hash"]}
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["error_code"] == "memory_changed" and detail["current"] == "- the agent wrote this\n"
    # Without a hash, the save is a deliberate overwrite.
    forced = await api.put(f"/api/v1/agents/{agent['id']}/memory", json={"text": "- mine\n"})
    assert forced.status_code == 200 and forced.json()["text"] == "- mine\n"


async def test_secrets_are_redacted_and_counted_on_save(api):
    agent = await make(api)
    saved = await api.put(
        f"/api/v1/agents/{agent['id']}/memory", json={"text": "key ghp_" + "a1" * 12 + "\n"}
    )
    assert saved.json()["text"] == "key [redacted]\n" and saved.json()["redacted"] == 1


async def test_oversized_notes_are_413(api):
    agent = await make(api)
    response = await api.put(
        f"/api/v1/agents/{agent['id']}/memory", json={"text": "x" * (agent_memory.FILE_MAX_BYTES + 1)}
    )
    assert response.status_code == 413 and code_of(response) == "memory_too_large"


async def test_the_notes_of_an_unknown_agent_are_404(api):
    assert (await api.get("/api/v1/agents/999999/memory")).status_code == 404
    assert (await api.put("/api/v1/agents/999999/memory", json={"text": ""})).status_code == 404


async def test_notes_can_be_switched_off_and_on(api):
    agent = await make(api, memory_enabled=False)
    assert agent["memory_enabled"] is False
    assert (await memory_of(api, agent))["enabled"] is False
    changed = await api.patch(f"/api/v1/agents/{agent['id']}", json={"memory_enabled": True})
    assert changed.json()["memory_enabled"] is True
    assert (await api.patch(f"/api/v1/agents/{agent['id']}", json={"memory_enabled": None})).status_code == 422


async def test_deleting_an_agent_deletes_its_notes_and_a_new_agent_never_inherits_them(api):
    agent = await make(api, starter_notes="- private")
    path = agent_memory.memory_path(agent["id"])
    assert path.exists()
    assert (await api.delete(f"/api/v1/agents/{agent['id']}")).status_code == 204
    assert not path.exists()
    # A file left at an id (say, by a crash) is cleared when an agent gets that id.
    path.write_text("- stale")
    agent_memory.write_memory(agent["id"] + 1, "- stale too")
    fresh = await make(api)
    assert (await memory_of(api, fresh))["text"] == ""


async def test_a_duplicate_copies_settings_but_not_notes(api):
    agent = await make(api, starter_notes="- mine", memory_enabled=False)
    copy = (await api.post(f"/api/v1/agents/{agent['id']}/duplicate")).json()
    assert copy["memory_enabled"] is False
    assert (await memory_of(api, copy))["text"] == ""


# --- what a turn is told --------------------------------------------------------------


async def test_each_turn_loads_the_current_notes(api, rt, sessions, launches):
    agent = await make(api, instructions="Be brief.", starter_notes="- The person is Ana\n")
    thread = (await thread_from(api, agent)).json()
    await send(api, rt, sessions, thread["id"], 1)
    prompt = launches[-1]["system_prompt"]
    assert prompt.startswith("Be brief.\n\n") and "- The person is Ana" in prompt

    await api.put(f"/api/v1/agents/{agent['id']}/memory", json={"text": "- The person is Bo\n"})
    await send(api, rt, sessions, thread["id"], 2)
    assert "- The person is Bo" in launches[-1]["system_prompt"]
    assert "Ana" not in launches[-1]["system_prompt"]


async def test_switched_off_notes_stay_out_of_the_turn(api, rt, sessions, launches):
    agent = await make(api, starter_notes="- hidden fact", memory_enabled=False)
    thread = (await thread_from(api, agent)).json()
    await send(api, rt, sessions, thread["id"], 1)
    assert launches[-1]["system_prompt"] is None


# --- export and import ----------------------------------------------------------------


async def test_an_agent_is_exported_without_model_folder_mode_or_notes(api, tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    agent = await make(
        api,
        title="Reviewer",
        instructions="Check tests. api_key=abcdef123456",
        model="opus",
        workspace_path=str(folder),
        approval_mode="accept_edits",
        starter_notes="- private note",
    )
    response = await api.get(f"/api/v1/agents/{agent['id']}/export")
    assert response.status_code == 200
    body = response.json()
    assert body["file_name"].endswith(".agent.json")
    assert body["redacted"] == ["instructions"]
    document = body["document"]
    assert document["agent"] == {
        "name": agent["name"],
        "title": "Reviewer",
        "instructions": "Check tests. api_key=[redacted]",
    }
    assert "notes" not in document
    for absent in ("opus", str(folder), "accept_edits", "private note"):
        assert absent not in response.text


async def test_notes_are_exported_only_when_asked(api):
    agent = await make(api, starter_notes="- keep this")
    body = (await api.get(f"/api/v1/agents/{agent['id']}/export?include_notes=true")).json()
    assert body["document"]["notes"] == {"MEMORY.md": "- keep this"}


async def test_an_import_starts_asking_on_defaults_with_a_free_name(api):
    source = await make(api, instructions="Do the thing.", model="opus", approval_mode="plan", starter_notes="- note")
    document = (await api.get(f"/api/v1/agents/{source['id']}/export?include_notes=true")).json()["document"]
    document["agent"]["approval_mode"] = "accept_edits"  # a hand-edited file
    document["agent"]["model"] = "opus"

    response = await api.post("/api/v1/agents/import", json={"document": document})
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["name"] == f"{source['name']} 2"
    assert imported["instructions"] == "Do the thing."
    assert imported["approval_mode"] == "ask"
    assert imported["model"] is None and imported["workspace_path"] is None and imported["pinned"] is False
    assert (await memory_of(api, imported))["text"] == "- note"


async def test_an_import_can_leave_the_files_notes_out(api):
    document = {
        "format": agent_package.FORMAT,
        "version": 1,
        "agent": {"name": "Imported " + uuid4().hex[:6]},
        "notes": {"MEMORY.md": "- not wanted"},
    }
    response = await api.post("/api/v1/agents/import", json={"document": document, "include_notes": False})
    assert response.status_code == 201
    assert (await memory_of(api, response.json()))["text"] == ""


async def test_something_that_is_not_an_agent_file_is_422(api):
    response = await api.post("/api/v1/agents/import", json={"document": {"format": "zip"}})
    assert response.status_code == 422 and code_of(response) == "invalid_agent_file"
    assert (await api.post("/api/v1/agents/import", json={})).status_code == 422
