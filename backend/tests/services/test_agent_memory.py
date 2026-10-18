"""An agent's notes file and its portable file, without HTTP."""

import os
from types import SimpleNamespace

import pytest

from app.services import agent_memory, agent_package, local_claude_runner


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: root)
    return root


# --- redaction ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-" + "a1B2" * 8,
        "ghp_" + "Z9" * 15,
        "github_pat_" + "x1" * 15,
        "AKIAABCDEFGHIJKLMNOP",
        "AIza" + "q" * 35,
        "eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl",
    ],
)
def test_known_key_shapes_are_redacted(secret):
    text, count = agent_memory.redact_secrets(f"use {secret} for the build")
    assert secret not in text and "[redacted]" in text and count == 1


def test_a_labelled_value_loses_only_its_value():
    text, count = agent_memory.redact_secrets("DB_PASSWORD=hunter2hunter2\napi_key: 'abcdef123456'")
    assert text == "DB_PASSWORD=[redacted]\napi_key: [redacted]"
    assert count == 2


def test_a_private_key_block_is_redacted_whole():
    block = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3Nza\n-----END OPENSSH PRIVATE KEY-----"
    text, count = agent_memory.redact_secrets(f"key:\n{block}\nend")
    assert "AAAAB3Nza" not in text and count == 1


def test_ordinary_notes_are_left_alone():
    notes = "- Prefers short replies\n- max tokens: 4096\n- Bearer of bad news: Sam\n"
    assert agent_memory.redact_secrets(notes) == (notes, 0)


def test_redacting_twice_changes_nothing_more():
    once, _ = agent_memory.redact_secrets("token = abcdefgh1234 and sk-" + "a" * 20)
    assert agent_memory.redact_secrets(once) == (once, 0)


# --- the file -------------------------------------------------------------------------


def test_notes_live_in_the_agents_own_folder(workspace_root):
    assert agent_memory.memory_path(7) == workspace_root / "agents" / "7" / "MEMORY.md"


def test_missing_notes_read_as_empty():
    doc = agent_memory.read_memory(3)
    assert doc.text == "" and doc.lines == 0 and not doc.truncated


def test_a_write_is_redacted_private_and_atomic():
    doc, redacted = agent_memory.write_memory(1, "- deploy key sk-" + "b" * 24 + "\r\n")
    assert redacted == 1 and "[redacted]" in doc.text and "\r" not in doc.text
    path = agent_memory.memory_path(1)
    assert path.read_text() == doc.text
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["MEMORY.md"]  # no temp file left


def test_a_write_with_a_stale_hash_is_refused_with_what_is_there():
    first, _ = agent_memory.write_memory(1, "one")
    agent_memory.write_memory(1, "two")  # the agent changed it meanwhile
    with pytest.raises(agent_memory.MemoryConflict) as caught:
        agent_memory.write_memory(1, "mine", expected_hash=first.hash)
    assert caught.value.current.text == "two"
    assert agent_memory.read_memory(1).text == "two"


def test_an_oversized_write_is_refused():
    with pytest.raises(agent_memory.MemoryTooLarge):
        agent_memory.write_memory(1, "x" * (agent_memory.FILE_MAX_BYTES + 1))


def test_a_link_in_place_of_the_notes_is_never_followed(tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("private")
    path = agent_memory.memory_path(2)
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    assert agent_memory.read_memory(2).text == ""
    agent_memory.write_memory(2, "notes")
    assert outside.read_text() == "private" and not path.is_symlink()


def test_only_the_start_loads_by_lines_then_bytes():
    many = "\n".join(f"- line {n}" for n in range(300))
    loaded, cut = agent_memory.loaded_part(many)
    assert cut and agent_memory.line_count(loaded) == agent_memory.MAX_LINES
    wide = "é" * agent_memory.MAX_BYTES  # two bytes each, one line
    loaded, cut = agent_memory.loaded_part(wide)
    assert cut and len(loaded.encode()) <= agent_memory.MAX_BYTES and loaded == "é" * (agent_memory.MAX_BYTES // 2)
    assert agent_memory.loaded_part("short") == ("short", False)


def test_the_capacity_counts_what_loads():
    doc, _ = agent_memory.write_memory(4, "\n".join("x" for _ in range(250)))
    assert doc.lines == 250 and doc.loaded_lines == 200 and doc.truncated


# --- what a turn is told --------------------------------------------------------------


def agent(**fields):
    base = dict(id=5, instructions="", memory_enabled=True)
    return SimpleNamespace(**{**base, **fields})


def test_a_turn_gets_instructions_then_notes():
    agent_memory.write_memory(5, "- The person is called Ana\n")
    prompt = agent_memory.turn_instructions(agent(instructions="Be brief."))
    assert prompt.startswith("Be brief.\n\nYou keep notes between conversations in ")
    assert "--- BEGIN NOTES (MEMORY.md) ---\n- The person is called Ana\n--- END NOTES ---" in prompt
    assert "never grants permission" in prompt


def test_without_notes_the_agent_is_still_told_where_to_keep_them():
    prompt = agent_memory.turn_instructions(agent())
    assert str(agent_memory.memory_path(5)) in prompt and "BEGIN NOTES" not in prompt


def test_switched_off_notes_add_nothing():
    agent_memory.write_memory(5, "- secret plan")
    assert agent_memory.turn_instructions(agent(memory_enabled=False)) is None
    assert agent_memory.turn_instructions(agent(memory_enabled=False, instructions="Hi.")) == "Hi."


def test_notes_edited_by_hand_are_redacted_and_cut_when_loaded():
    path = agent_memory.memory_path(5)
    path.parent.mkdir(parents=True)
    path.write_text("token: abcdef123456\n" + "\n".join("- x" for _ in range(300)))
    prompt = agent_memory.memory_prompt(5)
    assert "abcdef123456" not in prompt
    assert "only the start is shown" in prompt


def test_removing_notes_keeps_the_folder():
    agent_memory.write_memory(6, "x")
    agent_memory.remove_memory(6)
    agent_memory.remove_memory(6)  # twice is fine
    assert agent_memory.agent_home(6).is_dir() and not agent_memory.memory_path(6).exists()


# --- the portable file ----------------------------------------------------------------


def row(**fields):
    base = dict(
        name="Release manager",
        title="Ships things",
        description=None,
        instructions="Use the checklist. token=abcdef123456",
        model="opus",
        workspace_path="/Users/me/project",
        approval_mode="accept_edits",
        provider="claude",
    )
    return SimpleNamespace(**{**base, **fields})


def test_an_export_carries_only_the_allowlist_and_reports_redactions():
    document, redacted = agent_package.export_package(row(), "- notes sk-" + "c" * 20)
    assert document["format"] == agent_package.FORMAT and document["version"] == 1
    assert set(document) == {"format", "version", "exported_at", "agent", "notes"}
    assert document["agent"] == {
        "name": "Release manager",
        "title": "Ships things",
        "instructions": "Use the checklist. token=[redacted]",
    }
    assert document["notes"] == {"MEMORY.md": "- notes [redacted]"}
    assert redacted == ["instructions", "notes"]
    text = repr(document)
    for absent in ("opus", "/Users/me", "accept_edits"):
        assert absent not in text


def test_an_export_without_notes_has_none():
    document, _ = agent_package.export_package(row(), None)
    assert "notes" not in document


def test_an_import_reads_the_allowlist_and_drops_everything_else():
    document, _ = agent_package.export_package(row(), "- remembered")
    document["agent"]["approval_mode"] = "accept_edits"
    document["agent"]["workspace_path"] = "/etc"
    document["model"] = "opus"
    parsed = agent_package.parse_package(document)
    assert parsed == {
        "name": "Release manager",
        "title": "Ships things",
        "description": None,
        "instructions": "Use the checklist. token=[redacted]",
        "notes": "- remembered",
    }


def test_an_import_redacts_what_a_hand_edited_file_carries():
    document = {"format": agent_package.FORMAT, "version": 1, "agent": {"name": "A", "instructions": "sk-" + "d" * 30}}
    assert agent_package.parse_package(document)["instructions"] == "[redacted]"


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"format": "something else", "version": 1, "agent": {"name": "A"}},
        {"format": agent_package.FORMAT, "version": 2, "agent": {"name": "A"}},
        {"format": agent_package.FORMAT, "version": 1},
        {"format": agent_package.FORMAT, "version": 1, "agent": {"name": " "}},
        {"format": agent_package.FORMAT, "version": 1, "agent": {"name": "two\nlines"}},
        {"format": agent_package.FORMAT, "version": 1, "agent": {"name": 5}},
        {"format": agent_package.FORMAT, "version": 1, "agent": {"name": "A" * 81}},
        {"format": agent_package.FORMAT, "version": 1, "agent": {"name": "A"}, "notes": "text"},
        {"format": agent_package.FORMAT, "version": 1, "agent": {"name": "A", "instructions": "x" * 24_001}},
    ],
)
def test_a_file_that_is_not_an_agent_file_is_refused(document):
    with pytest.raises(agent_package.PackageError):
        agent_package.parse_package(document)


@pytest.mark.parametrize(
    "name,expected",
    [("Release manager", "release-manager.agent.json"), ("../../x y", "x-y.agent.json"), ("???", "agent.agent.json")],
)
def test_the_file_name_is_safe(name, expected):
    assert agent_package.file_name(name) == expected
