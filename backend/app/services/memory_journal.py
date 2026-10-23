"""A record of every change made to an agent's notes through this app, with
undo (docs/MEMORY.md).

Each row says which file (`MEMORY.md` or `memory/<topic>.md`), who changed it
(the person in the Agents panel, an accepted proposal, an undo), when, the
before and after hashes, a short diff, and the earlier text so Undo can put the
file back. An undo is itself a change and can be undone.

The journal lives outside the agent's folder, under the app's data folder, so
an agent with file tools working in its own folder cannot rewrite the record of
its own notes. Earlier text is kept redacted; a row whose earlier text had
something redacted, or was too large to keep, cannot be undone and says why.
Changes an agent makes with its own file tools are not seen here (a gap noted
in docs/MEMORY.md). A journal write never fails the change it records.
"""

import difflib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from app.core.logging import get_logger
from app.services import agent_memory, memory_topics
from app.services.agent_memory import redact_secrets, text_hash

logger = get_logger(__name__)

DIFF_MAX_CHARS = 4_000
BEFORE_MAX_BYTES = 256 * 1024
READ_MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 200


class JournalEntryNotFound(LookupError):
    pass


class CannotUndo(ValueError):
    pass


def state_root() -> Path:
    """The app's own data folder for memory bookkeeping (the journal and the
    proposed-notes queue): beside the attachments folder, never inside an
    agent's folder."""
    from app.services import attachments

    return attachments.root_dir().parent / "memory-state"


def journal_path(agent_id: int) -> Path:
    return state_root() / "journal" / f"{int(agent_id)}.ndjson"


def note_path(name: str) -> str:
    """How a row names a file: `MEMORY.md` or `memory/<topic>.md`."""
    return agent_memory.MEMORY_FILE if name == agent_memory.MEMORY_FILE else f"{memory_topics.TOPICS_DIR}/{name}"


def _diff(before: str, after: str, path: str) -> tuple[str, int, int]:
    lines = list(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=1
        )
    )
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    text = "\n".join(lines)
    if len(text) > DIFF_MAX_CHARS:
        text = text[:DIFF_MAX_CHARS] + "\n… (diff cut here)"
    text, _ = redact_secrets(text)
    return text, added, removed


def record(
    agent_id: int, path: str, *, actor: str, before: str | None, after: str | None, via: str | None = None
) -> dict | None:
    """Append one row; None when nothing changed or the row could not be
    written (logged, never raised). `via` names the upkeep step that wrote it
    ("capture", "tidy" or "organize")."""
    if before == after:
        return None
    kind = "created" if before is None else "deleted" if after is None else "edited"
    diff, added, removed = _diff(before or "", after or "", path)
    kept: str | None = None
    can_undo = True
    reason: str | None = None
    if before is not None:
        if len(before.encode("utf-8")) > BEFORE_MAX_BYTES:
            can_undo, reason = False, "The earlier version was too large to keep."
        else:
            kept, redacted = redact_secrets(before)
            if redacted:
                can_undo, reason = False, "The earlier version held text that looked like a secret, so it was not kept."
    row = {
        "id": uuid4().hex,
        "at": time.time(),
        "path": path,
        "actor": actor,
        "via": via,
        "kind": kind,
        "before_hash": None if before is None else text_hash(before),
        "after_hash": None if after is None else text_hash(after),
        "before": kept,
        "diff": diff,
        "added": added,
        "removed": removed,
        "can_undo": can_undo,
        "reason": reason,
    }
    try:
        target = journal_path(agent_id)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    except OSError:
        logger.exception("could not write the memory journal", agent_id=agent_id)
        return None
    return row


def read(agent_id: int, limit: int = 50) -> list[dict]:
    """The newest rows first."""
    try:
        with journal_path(agent_id).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - READ_MAX_BYTES))
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict] = []
    for line in reversed(data.splitlines()):
        if len(rows) >= max(1, min(limit, MAX_ROWS)):
            break
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("path"), str):
            rows.append(row)
    return rows


def remove(agent_id: int) -> None:
    try:
        journal_path(agent_id).unlink()
    except OSError:
        pass


def snapshot(agent_id: int) -> dict[str, str | None]:
    """What every memory file says right now (MEMORY.md and each topic note),
    for `record_changes` at the end of a turn."""
    out: dict[str, str | None] = {agent_memory.MEMORY_FILE: agent_memory.read_memory(agent_id).text}
    for topic in memory_topics.list_topics(agent_id):
        out[note_path(topic.name)] = memory_topics.read_topic(agent_id, topic.name).text
    return out


def record_changes(agent_id: int, before: dict[str, str | None], *, actor: str = "agent") -> int:
    """Journal what differs from `before`: the changes an agent made with its
    own file tools during a turn. A file whose newest row already ends in its
    current text (the app wrote it and journaled it) is skipped. Returns the
    number of rows written."""
    after = snapshot(agent_id)
    rows = 0
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        last = next((r for r in read(agent_id, 20) if r.get("path") == path), None)
        if last is not None and last.get("after_hash") == (None if new is None else text_hash(new)):
            continue
        if record(agent_id, path, actor=actor, before=old, after=new) is not None:
            rows += 1
    return rows


def _current(agent_id: int, path: str) -> str | None:
    if path == agent_memory.MEMORY_FILE:
        return agent_memory.read_memory(agent_id).text
    doc = memory_topics.read_topic(agent_id, path.split("/", 1)[1])
    return doc.text if doc.exists else None


def undo(agent_id: int, entry_id: str) -> dict | None:
    """Put the file back the way it was before that row's change. Returns the
    new row. Raises JournalEntryNotFound or CannotUndo."""
    row = next((r for r in read(agent_id, MAX_ROWS) if r.get("id") == entry_id), None)
    if row is None:
        raise JournalEntryNotFound(entry_id)
    if not row.get("can_undo"):
        raise CannotUndo(row.get("reason") or "This change cannot be undone.")
    path = row["path"]
    if path != agent_memory.MEMORY_FILE:
        if not path.startswith(f"{memory_topics.TOPICS_DIR}/"):
            raise CannotUndo("This row does not name a note.")
        memory_topics.check_name(path.split("/", 1)[1])
    before_now = _current(agent_id, path)
    target = row.get("before")
    if path == agent_memory.MEMORY_FILE:
        doc, _ = agent_memory.write_memory(agent_id, target or "")
        after = doc.text
    elif target is None:
        memory_topics.delete_topic(agent_id, path.split("/", 1)[1])
        after = None
    else:
        doc, _, _ = memory_topics.write_topic(agent_id, path.split("/", 1)[1], target)
        after = doc.text
    return record(agent_id, path, actor="undo", before=before_now, after=after)
