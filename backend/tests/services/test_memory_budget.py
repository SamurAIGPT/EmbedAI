"""MEMORY.md stays inside its budget by archiving, entries expire, replaced
entries are skipped, and the archive is never recalled (docs/MEMORY.md)."""

import json

import pytest

from app.services import (
    agent_memory,
    local_claude_runner,
    memory_budget,
    memory_capture,
    memory_entries,
    memory_journal,
    memory_proposals,
    memory_recall,
    memory_topics,
)
from app.services.memory_capture import Fact

DAY = "2026-10-23"


@pytest.fixture(autouse=True)
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setattr(local_claude_runner, "workspace_dir", lambda: root)
    return root


def entry(day, text, extra=""):
    return f'- {day} · from chat "A" · {text}{extra}'


def room(text, **kw):
    return memory_entries.make_room(text, kw.pop("on", DAY), kw.pop("keep", set()), max_lines=kw.pop("lines", 4), max_bytes=10_000)


# --- the rule ------------------------------------------------------------------------


def test_struck_then_expired_then_oldest_live_move_first():
    lines = [
        entry("2026-01-05", "oldest live"),
        entry("2026-03-01", "struck", " · superseded 2026-03-02").replace("struck", "~~struck~~"),
        entry("2026-02-01", "expired", " · until 2026-05-01"),
        entry("2026-02-02", "newer live"),
        entry("2026-10-22", "newest"),
        entry("2026-10-23", "just written"),
    ]
    text = "\n".join(lines) + "\n"
    kept, moved = room(text, keep={lines[-1]}, lines=4)
    assert len(moved) == 2 and "~~struck~~" in moved[0] and "expired" in moved[1]  # in file order: struck is the newer
    assert "oldest live" in kept  # two moves were enough
    kept, moved = room(text, keep={lines[-1]}, lines=3)
    assert len(moved) == 3 and "oldest live" in "\n".join(moved)
    assert "just written" in kept and "newest" in kept


def test_hand_written_lines_health_facts_and_the_new_entry_never_move():
    text = "\n".join([
        "# My notes",
        "hand written one",
        "hand written two",
        entry("2026-01-01", "The person is allergic to peanuts"),
        entry("2026-01-02", "old plain fact"),
        entry("2026-10-23", "new fact"),
    ]) + "\n"
    kept, moved = room(text, keep={entry("2026-10-23", "new fact")}, lines=5)
    assert moved == [entry("2026-01-02", "old plain fact")]
    assert "allergic to peanuts" in kept and "hand written two" in kept


def test_when_moving_everything_would_not_help_nothing_moves():
    text = "\n".join(["hand written"] * 6 + [entry("2026-01-01", "a")]) + "\n"
    assert room(text, lines=4) == (text, [])


def test_an_entry_moves_whole_with_its_continuation_lines():
    text = "\n".join([
        entry("2026-01-01", "has a block:"),
        "  indented detail",
        "  ```",
        "  code",
        "  ```",
        entry("2026-10-23", "fresh"),
    ]) + "\n"
    kept, moved = room(text, keep={entry("2026-10-23", "fresh")}, lines=2)
    assert moved[0].count("\n") == 4 and kept.strip() == entry("2026-10-23", "fresh")


def test_entry_caps():
    memory_entries.check_entry("x" * 1000)
    with pytest.raises(memory_entries.EntryTooLarge):
        memory_entries.check_entry("x" * 1001)
    with pytest.raises(memory_entries.EntryTooLarge):
        memory_entries.check_entry("\n".join(["a"] * 21))


# --- through the app's own writes -------------------------------------------------------


def fill(agent_id, count):
    text = "\n".join(entry(f"2026-{1 + n // 28:02d}-{1 + n % 28:02d}", f"fact number {n}") for n in range(count)) + "\n"
    agent_memory.write_memory(agent_id, "# hand written\n" + text)


def test_a_noticed_fact_archives_the_oldest_instead_of_overflowing(monkeypatch):
    fill(1, 199)  # 200 lines with the heading: full
    memory_capture.file_facts(1, [Fact("The person moved to Pune", "fact")], source='chat "Moving"', day=DAY)
    doc = agent_memory.read_memory(1)
    assert doc.lines <= agent_memory.MAX_LINES and not doc.truncated
    assert "moved to Pune" in doc.text and doc.text.startswith("# hand written")
    assert "fact number 0" not in doc.text
    archive = memory_topics.read_topic(1, "archive.md").text
    assert f"fact number 0 · moved {DAY}" in archive
    # The archive is journaled before MEMORY.md, so undoing the newest row loses nothing.
    paths = [r["path"] for r in reversed(memory_journal.read(1))]
    assert paths == ["memory/archive.md", "MEMORY.md"]
    memory_journal.undo(1, memory_journal.read(1)[0]["id"])
    assert "fact number 0" in agent_memory.read_memory(1).text and "fact number 0" in memory_topics.read_topic(1, "archive.md").text


def test_the_agent_is_told_what_moved():
    fill(1, 199)
    memory_capture.file_facts(1, [Fact("The person moved to Pune", "fact")], source='chat "Moving"', day=memory_entries.today())
    prompt = agent_memory.memory_prompt(1)
    assert "moved to memory/archive.md" in prompt and "fact number 0" in prompt
    assert "older notes moved out of MEMORY.md" in prompt  # the archive is in the topic index


def test_an_accepted_proposal_is_kept_within_budget_and_over_long_lines_are_refused():
    fill(1, 199)
    added = memory_proposals.propose(1, thread_id=1, title="T", turns=[(1, "I prefer aisle seats on every flight")])
    memory_proposals.accept(1, added[0]["id"])
    doc = agent_memory.read_memory(1)
    assert "aisle seats" in doc.text and doc.lines <= agent_memory.MAX_LINES
    assert "fact number 0" not in doc.text
    more = memory_proposals.propose(1, thread_id=1, title="T", turns=[(2, "I prefer window seats on a night flight")])
    with pytest.raises(memory_entries.EntryTooLarge):
        memory_proposals.accept(1, more[0]["id"], line="x" * 1001)
    assert memory_proposals.pending(1)  # still waiting: a refused accept loses nothing


def test_a_file_full_of_lines_that_never_move_is_saved_over_budget():
    agent_memory.write_memory(1, "\n".join(f"hand written {n}" for n in range(200)) + "\n")
    memory_capture.file_facts(1, [Fact("The person moved to Pune", "fact")], source='chat "A"', day=DAY)
    doc = agent_memory.read_memory(1)
    assert "moved to Pune" in doc.text and doc.truncated  # the gauge goes red; nothing was lost


# --- expiry, superseded, recall ---------------------------------------------------------


def test_expired_entries_stop_loading_but_stay_in_the_file():
    today = memory_entries.today()
    text = "\n".join([
        entry("2026-01-01", "still true"),
        entry("2026-01-02", "ended", " · until 2026-01-03"),
        entry("2026-01-02", "ends today", f" · until {today}"),
    ]) + "\n"
    doc = agent_memory.write_memory(1, text)[0]
    assert doc.expired == 1 and doc.expired_lines == 1 and "ended" in doc.text
    prompt = agent_memory.memory_prompt(1)
    notes = prompt.split("--- BEGIN NOTES")[1]
    assert "still true" in notes and "ends today" in notes and "ended" not in notes
    assert "1 expired note is not shown" in prompt


def test_expiry_is_applied_before_the_cut():
    old = "\n".join(entry("2026-01-01", f"gone {n}", " · until 2026-01-02") for n in range(250))
    agent_memory.write_memory(1, old + "\n" + entry("2026-02-01", "kept at the end") + "\n")
    doc = agent_memory.read_memory(1)
    assert not doc.truncated and "kept at the end" in agent_memory.memory_prompt(1)


def test_recall_skips_superseded_expired_and_the_archive():
    memory_topics.write_topic(1, "home.md", "\n".join([
        entry("2026-01-01", "~~The office is in Pune~~", " · superseded 2026-02-01"),
        entry("2026-02-01", "The office is in Mumbai"),
        entry("2026-02-01", "Office guest wifi guest-pass is posted", " · until 2026-03-01"),
    ]) + "\n")
    memory_topics.write_topic(1, "archive.md", entry("2026-01-01", "Office parking permit number 4411") + "\n")
    found = memory_recall.note_passages(1, memory_recall.terms_of("where is the office parking and wifi"))
    names = [n["name"] for n in found]
    assert names == ["home.md"]
    snippet = found[0]["snippet"]
    assert "Mumbai" in snippet and "Pune" not in snippet and "guest-pass" not in snippet
