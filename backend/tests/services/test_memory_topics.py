"""Topic notes, the memory journal and proposed notes, without HTTP
(docs/MEMORY.md)."""

import os
import stat

import pytest

from app.services import agent_memory, local_claude_runner, memory_journal, memory_proposals, memory_topics
from app.services.agent_memory import MemoryConflict, MemoryTooLarge, text_hash


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: root)
    return root


# --- names ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["travel.md", "Asha_2.md", "client-acme.md"])
def test_plain_file_names_are_topics(name):
    assert memory_topics.check_name(name) == name


@pytest.mark.parametrize(
    "name", ["../x.md", "a/b.md", ".hidden.md", "travel", "travel.txt", "a.b.md", "MEMORY.md", "memory.md", "x" * 70 + ".md", "", "travel.md\n"]
)
def test_anything_else_is_refused(name):
    with pytest.raises(memory_topics.InvalidTopicName):
        memory_topics.check_name(name)


def test_a_subject_becomes_a_file_name():
    assert memory_topics.topic_name_for("Asha's School") == "asha-s-school.md"
    with pytest.raises(memory_topics.InvalidTopicName):
        memory_topics.topic_name_for("!!!")


# --- saving and reading ---------------------------------------------------------------


def test_a_note_is_saved_atomically_owner_only_and_redacted():
    doc, redacted, before = memory_topics.write_topic(7, "travel.md", "# Travel\n- aisle seat\n- token=abcdef123456\n")
    assert before is None and redacted == 1
    assert doc.exists and "abcdef123456" not in doc.text and "[redacted]" in doc.text
    path = memory_topics.topics_dir(7) / "travel.md"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


def test_a_save_carries_the_hash_it_was_based_on():
    empty = memory_topics.read_topic(7, "food.md")
    assert not empty.exists and empty.hash == text_hash("")
    doc, _, _ = memory_topics.write_topic(7, "food.md", "- likes dosa\n", expected_hash=empty.hash)
    memory_topics.write_topic(7, "food.md", "- the agent changed this\n")
    with pytest.raises(MemoryConflict) as refused:
        memory_topics.write_topic(7, "food.md", "- mine\n", expected_hash=doc.hash)
    assert refused.value.current.text == "- the agent changed this\n"


def test_size_and_count_are_capped(monkeypatch):
    with pytest.raises(MemoryTooLarge):
        memory_topics.write_topic(7, "big.md", "x" * (memory_topics.TOPIC_MAX_BYTES + 1))
    monkeypatch.setattr(memory_topics, "MAX_TOPICS", 2)
    memory_topics.write_topic(7, "a.md", "a")
    memory_topics.write_topic(7, "b.md", "b")
    with pytest.raises(memory_topics.TooManyTopics):
        memory_topics.write_topic(7, "c.md", "c")
    memory_topics.write_topic(7, "a.md", "a again")  # an existing note can still change


def test_a_link_never_reads_another_file(tmp_path):
    secret = tmp_path / "elsewhere.md"
    secret.write_text("not yours")
    folder = memory_topics.topics_dir(7)
    folder.mkdir(parents=True)
    (folder / "sneaky.md").symlink_to(secret)
    assert memory_topics.read_topic(7, "sneaky.md").text == ""
    assert [t.name for t in memory_topics.list_topics(7)] == []


def test_a_linked_memory_folder_reads_as_no_topics(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.md").write_text("outside")
    agent_memory.agent_home(8).mkdir(parents=True)
    memory_topics.topics_dir(8).symlink_to(other)
    assert memory_topics.list_topics(8) == []
    with pytest.raises(memory_topics.InvalidTopicName):
        memory_topics.write_topic(8, "x.md", "overwrite")
    assert (other / "x.md").read_text() == "outside"


def test_header_gives_title_and_aliases():
    text = "---\ntitle: Asha\naliases: [sister, sibling]\n---\n- school in Pune\n"
    header = memory_topics.parse_header(text)
    assert header.title == "Asha" and header.aliases == ("sister", "sibling")
    assert memory_topics.body_of(text) == "- school in Pune"
    assert memory_topics.parse_header("# Travel plans\n- x").title == "Travel plans"
    listed = "---\naliases:\n  - trips\n  - flights\n---\nbody"
    assert memory_topics.parse_header(listed).aliases == ("trips", "flights")
    quoted = '---\ntitle: People\naliases: ["Smith, Jr", sibling]\n---\nbody\n'
    assert memory_topics.parse_header(quoted).aliases == ("Smith, Jr", "sibling")


def test_the_index_lists_topics_and_the_prompt_carries_it():
    memory_topics.write_topic(7, "travel.md", "---\ntitle: Trips\naliases: [flights]\n---\n- aisle\n")
    memory_topics.write_topic(7, "asha.md", "# Asha\n- sister\n")
    index = memory_topics.topic_index(7)
    assert index.splitlines() == ["- memory/asha.md — Asha", "- memory/travel.md — Trips (also: flights)"]
    prompt = agent_memory.memory_prompt(7)
    assert "--- TOPIC NOTES ---" in prompt and "memory/travel.md" in prompt
    assert "- aisle" not in prompt  # topic bodies are never loaded whole


def test_the_index_is_bounded(monkeypatch):
    monkeypatch.setattr(memory_topics, "INDEX_MAX_TOPICS", 2)
    for name in ("a.md", "b.md", "c.md"):
        memory_topics.write_topic(7, name, "x")
    assert memory_topics.topic_index(7).splitlines()[-1] == "- …and 1 more in memory/"


def test_removing_an_agents_memory_removes_topics_journal_and_proposals():
    memory_topics.write_topic(9, "a.md", "x")
    memory_journal.record(9, "memory/a.md", actor="person", before=None, after="x")
    memory_proposals.propose(9, thread_id=1, title="t", turns=[(1, "Remember that I take the 8am train")])
    agent_memory.remove_memory(9)
    assert memory_topics.list_topics(9) == [] and memory_journal.read(9) == [] and memory_proposals.pending(9) == []


# --- the journal ----------------------------------------------------------------------


def test_every_change_is_a_row_and_undo_puts_the_file_back():
    memory_topics.write_topic(7, "trip.md", "- Lisbon\n")
    memory_journal.record(7, "memory/trip.md", actor="person", before=None, after="- Lisbon\n")
    memory_topics.write_topic(7, "trip.md", "- Porto\n")
    edit = memory_journal.record(7, "memory/trip.md", actor="person", before="- Lisbon\n", after="- Porto\n")
    assert edit["kind"] == "edited" and edit["added"] == 1 and edit["removed"] == 1
    rows = memory_journal.read(7)
    assert [r["kind"] for r in rows] == ["edited", "created"]
    undo = memory_journal.undo(7, edit["id"])
    assert memory_topics.read_topic(7, "trip.md").text == "- Lisbon\n"
    assert undo["actor"] == "undo"
    # Undoing the creation deletes the note; that too is a row.
    memory_journal.undo(7, rows[1]["id"])
    assert not memory_topics.read_topic(7, "trip.md").exists


def test_undo_of_memory_md_rewrites_it():
    agent_memory.write_memory(7, "- one\n")
    row = memory_journal.record(7, "MEMORY.md", actor="person", before="", after="- one\n")
    memory_journal.undo(7, row["id"])
    assert agent_memory.read_memory(7).text == ""


def test_an_earlier_version_with_a_secret_is_not_kept_and_cannot_be_undone():
    row = memory_journal.record(7, "MEMORY.md", actor="person", before="password=hunter2hunter2", after="")
    assert row["can_undo"] is False and "hunter2" not in str(memory_journal.read(7))
    with pytest.raises(memory_journal.CannotUndo):
        memory_journal.undo(7, row["id"])
    with pytest.raises(memory_journal.JournalEntryNotFound):
        memory_journal.undo(7, "nope")


def test_the_journal_lives_outside_the_agents_folder(attachments_dir):
    memory_journal.record(7, "MEMORY.md", actor="person", before="", after="x")
    path = memory_journal.journal_path(7)
    assert path.exists() and agent_memory.agent_home(7) not in path.parents
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_nothing_changed_is_no_row():
    assert memory_journal.record(7, "MEMORY.md", actor="person", before="x", after="x") is None


# --- proposed notes -------------------------------------------------------------------


@pytest.mark.parametrize(
    "message, expected",
    [
        ("Please remember that my flight is on Friday.", ["my flight is on Friday"]),
        ("From now on, answer in Spanish", ["answer in Spanish"]),
        ("I prefer aisle seats. Thanks!", ["I prefer aisle seats"]),
        ("my dentist is Dr Rao", ["my dentist is Dr Rao"]),
        ("Do I prefer aisle seats?", []),
        ("what is the weather", []),
    ],
)
def test_lasting_statements_are_found_in_the_persons_words(message, expected):
    assert memory_proposals.statements(message) == expected


def test_proposals_wait_for_the_person_and_are_never_written_on_their_own():
    added = memory_proposals.propose(7, thread_id=3, title="Trip", turns=[(11, "I prefer aisle seats")])
    assert len(added) == 1 and added[0]["line"].endswith('· The person said: "I prefer aisle seats"')
    assert agent_memory.read_memory(7).text == ""  # nothing saved yet
    # The same line is not proposed twice.
    assert memory_proposals.propose(7, thread_id=3, title="Trip", turns=[(12, "I prefer aisle seats.")]) == []


def test_accept_appends_and_is_journaled_reject_never_comes_back():
    agent_memory.write_memory(7, "- existing\n")
    a, b = memory_proposals.propose(7, thread_id=3, title="T", turns=[(1, "I prefer tea. I live in Pune")])
    result = memory_proposals.accept(7, a["id"])
    assert result["path"] == "MEMORY.md"
    assert agent_memory.read_memory(7).text == f"- existing\n{a['line']}\n"
    assert memory_journal.read(7)[0]["actor"] == "proposal"
    memory_proposals.reject(7, b["id"])
    assert memory_proposals.pending(7) == []
    assert memory_proposals.propose(7, thread_id=4, title="T", turns=[(2, "I live in Pune")]) == []
    with pytest.raises(memory_proposals.ProposalNotFound):
        memory_proposals.accept(7, a["id"])


def test_accept_into_a_topic_with_an_edited_line():
    (p,) = memory_proposals.propose(7, thread_id=3, title="T", turns=[(1, "Remember that Asha starts school in June")])
    memory_proposals.accept(7, p["id"], line="- Asha starts school in June", target="asha.md")
    assert memory_topics.read_topic(7, "asha.md").text == "- Asha starts school in June\n"


def test_a_statement_with_a_secret_is_never_proposed():
    assert memory_proposals.propose(7, thread_id=3, title="T", turns=[(1, "Remember that my api_key=abcdef123456")]) == []


def test_a_statement_already_in_the_notes_is_not_proposed():
    agent_memory.write_memory(7, "- I prefer aisle seats\n")
    assert memory_proposals.propose(7, thread_id=3, title="T", turns=[(1, "I prefer aisle seats")]) == []
