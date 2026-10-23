"""MEMORY.md never overflows through the app (docs/MEMORY.md).

Only the first 200 lines / 24 KB of MEMORY.md load into a turn, so a line added
beyond that would silently never be seen. Every write the app makes on its own
(a noticed fact, an accepted proposal) therefore keeps the file inside the
budget in the same step: when the new lines would push it past, the oldest
dated entries move whole to `memory/archive.md`, each marked `· moved <day>`;
struck-through (superseded) entries first, then expired ones, then the oldest
live ones (memory_entries.make_room). Lines a person wrote by hand, live health
and safety facts, and the lines just written never move.

The archive is written, and journaled, before MEMORY.md, so a line is never
out of one without being in the other, and undoing the newest change never
loses a line. The agent is told what moved on its next turn (agent_memory.
memory_prompt). The archive is never recalled; it stays readable as a topic
note.
"""

from dataclasses import dataclass
from datetime import datetime

from app.core.logging import get_logger
from app.services import agent_memory, memory_entries, memory_journal, memory_status, memory_topics
from app.services.agent_memory import MemoryConflict, MemoryTooLarge

logger = get_logger(__name__)

ARCHIVE = memory_topics.ARCHIVE_NAME
ARCHIVE_TITLE = "Archive"
# How long the agent is reminded that entries moved.
NOTICE_DAYS = 3
NOTICE_ENTRIES = 3


@dataclass(frozen=True)
class Appended:
    added: int
    moved: int


def _archive_text(before: str | None, moved: list[str], day: str) -> str:
    marked = [m.split("\n", 1)[0] + f" · moved {day}" + ("\n" + m.split("\n", 1)[1] if "\n" in m else "") for m in moved]
    head = before if before is not None else f"---\ntitle: {ARCHIVE_TITLE}\n---\n\n"
    return head + ("" if head.endswith("\n") else "\n") + "\n".join(marked) + "\n"


def append_lines(
    agent_id: int, lines: list[str], *, actor: str, day: str | None = None, skip_known: bool = True
) -> Appended:
    """Append dated entries to MEMORY.md, moving older entries to the archive
    when the file would go past its budget. Entries already in the file (same
    fact) are skipped. Raises MemoryTooLarge when the file itself is at its
    hard size cap."""
    day = day or memory_entries.today()
    for _ in range(2):
        doc = agent_memory.read_memory(agent_id)
        have = memory_entries.identities(doc.text) if skip_known else set()
        fresh = []
        for line in lines:
            key = memory_entries.line_identity(line)
            if key and key in have:
                continue
            have.add(key)
            fresh.append(line)
        if not fresh:
            return Appended(0, 0)
        base = doc.text.rstrip("\n") + "\n" if doc.text.strip() else ""
        text = base + "\n".join(fresh) + "\n"
        kept, moved = memory_entries.make_room(
            text, day, {line.split("\n", 1)[0] for line in fresh},
            max_lines=agent_memory.MAX_LINES, max_bytes=agent_memory.MAX_BYTES,
        )
        archive_before = None
        archive_path = memory_journal.note_path(ARCHIVE)
        if moved:
            current = memory_topics.read_topic(agent_id, ARCHIVE)
            archive_before = current.text if current.exists else None
            try:
                memory_topics.write_topic(agent_id, ARCHIVE, _archive_text(archive_before, moved, day))
            except (MemoryTooLarge, memory_topics.TooManyTopics):
                # No room in the archive either: keep every line, over budget, and say nothing moved.
                kept, moved = text, []
        try:
            new, _ = agent_memory.write_memory(agent_id, kept, expected_hash=doc.hash)
        except MemoryConflict:
            if moved:  # the agent changed the file meanwhile: undo the archive write, read again
                _restore_archive(agent_id, archive_before)
            continue
        except MemoryTooLarge:
            if moved:
                _restore_archive(agent_id, archive_before)
            raise
        if moved:
            after = memory_topics.read_topic(agent_id, ARCHIVE).text
            memory_journal.record(agent_id, archive_path, actor=actor, before=archive_before, after=after)
        memory_journal.record(agent_id, agent_memory.MEMORY_FILE, actor=actor, before=doc.text, after=new.text)
        if moved:
            _note_moved(agent_id, moved, day)
        return Appended(len(fresh), len(moved))
    return Appended(0, 0)


def _restore_archive(agent_id: int, before: str | None) -> None:
    try:
        if before is None:
            memory_topics.delete_topic(agent_id, ARCHIVE)
        else:
            memory_topics.write_topic(agent_id, ARCHIVE, before)
    except Exception:  # noqa: BLE001
        logger.exception("could not put the archive back", agent_id=agent_id)


def _note_moved(agent_id: int, moved: list[str], day: str) -> None:
    entries = [memory_entries.parse(m)[0] for m in moved if memory_entries.parse(m)]
    first = [" ".join(e.body.split())[:80] for e in entries[:NOTICE_ENTRIES]]
    history = memory_status.read(agent_id).get("moved") or []
    history.append({"day": day, "count": len(moved), "first": first})
    memory_status.update(agent_id, moved=history[-5:])


def moved_notice(agent_id: int, today: str | None = None) -> str:
    """What the agent is told about entries moved to the archive lately, or ""."""
    today = today or memory_entries.today()
    try:
        limit = datetime.strptime(today, "%Y-%m-%d").toordinal() - NOTICE_DAYS
    except ValueError:
        return ""
    recent = [
        m for m in (memory_status.read(agent_id).get("moved") or [])
        if isinstance(m, dict) and isinstance(m.get("day"), str) and memory_entries.is_day(m["day"])
        and datetime.strptime(m["day"], "%Y-%m-%d").toordinal() >= limit
    ]
    if not recent:
        return ""
    count = sum(int(m.get("count") or 0) for m in recent)
    first = [t for m in recent for t in (m.get("first") or []) if isinstance(t, str)][:NOTICE_ENTRIES]
    shown = "; ".join(f'"{t}"' for t in first)
    return (
        f"To keep these notes within their size, {count} older "
        f"{'entry was' if count == 1 else 'entries were'} moved to memory/{ARCHIVE} "
        f"(for example {shown}). Add back anything that should stay loaded."
    )
