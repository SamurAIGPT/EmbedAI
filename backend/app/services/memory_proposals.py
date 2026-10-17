"""Proposed notes: lines an agent's memory upkeep suggests saving, waiting for
the person to accept or reject them (docs/MEMORY.md).

Nothing here writes to an agent's notes on its own. Once a conversation with
the agent has been quiet for `QUIET_SECONDS`, the person's own messages in it
are read for statements meant to last ("remember that ...", "from now on ...",
"I prefer ...", "my ... is ..."), and each one becomes a proposal in a queue
beside the journal. The person sees the queue in the Agents panel: Accept
appends the line to MEMORY.md (or a topic note) through the same save as the
editor, journaled and undoable; Reject drops it, and the same line is never
proposed again.

The proposer is deterministic (no model call): it quotes the person's words
rather than rewriting them, so it can miss facts said in other ways, and it
never reads the agent's replies. A line that held anything that looked like a
secret is never proposed.
"""

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.logging import get_logger
from app.services import agent_memory, memory_journal, memory_topics
from app.services.agent_memory import redact_secrets

logger = get_logger(__name__)

QUIET_SECONDS = 120
MAX_PENDING = 50
MAX_DECIDED = 500
MAX_PER_CONVERSATION = 5
SENTENCE_MAX = 300
# How many quiet conversations one look reads, newest first.
SCAN_THREADS = 20

_LASTING = (
    re.compile(r"\b(?:please\s+)?remember(?:\s+that)?\s*[:,]?\s+(?P<s>[^.!?\n]{6,})", re.I),
    re.compile(r"\b(?:from now on|going forward|in future|in the future)\s*,?\s+(?P<s>[^.!?\n]{6,})", re.I),
    re.compile(
        r"(?P<s>\bI\s+(?:always|never|usually|prefer|like|love|hate|dislike|don't like|do not like|"
        r"am allergic to|am vegetarian|am vegan|live in|work at|work for|work as)\b[^.!?\n]{2,})",
        re.I,
    ),
    re.compile(r"(?P<s>\bmy\s+[a-z][a-z' -]{1,30}\s+(?:is|are)\s+[^.!?\n]{2,})", re.I),
)
# A question is not a statement to keep.
_QUESTION = re.compile(r"^\s*(?:do|does|did|can|could|would|will|should|is|are|what|why|how|when|where|who)\b", re.I)


class ProposalNotFound(LookupError):
    pass


def _path(agent_id: int) -> Path:
    return memory_journal.state_root() / "proposals" / f"{int(agent_id)}.json"


def _load(agent_id: int) -> dict:
    try:
        data = json.loads(_path(agent_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("items", [])
    data.setdefault("scanned", {})
    data.setdefault("decided", [])
    return data


def _save(agent_id: int, data: dict) -> None:
    data["decided"] = data["decided"][-MAX_DECIDED:]
    target = _path(agent_id)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    agent_memory.write_atomic(target, json.dumps(data))


def remove(agent_id: int) -> None:
    try:
        _path(agent_id).unlink()
    except OSError:
        pass


def identity(text: str) -> str:
    """Two lines are the same proposal when they differ only in case,
    spacing and a final full stop."""
    return " ".join(text.lower().split()).rstrip(".")


def statements(text: str) -> list[str]:
    """The lasting statements in one message of the person's, in order.
    Questions are never statements."""
    found: list[str] = []
    for raw in re.findall(r"[^.!?\n]+[.!?]?", text or ""):
        sentence_text = raw.strip()
        if not sentence_text or sentence_text.endswith("?") or _QUESTION.match(sentence_text):
            continue
        for pattern in _LASTING:
            match = pattern.search(sentence_text)
            if not match:
                continue
            sentence = " ".join(match.group("s").split()).strip(" ,;:-.!")[:SENTENCE_MAX]
            if len(sentence) < 6:
                continue
            if any(identity(sentence) in identity(f) or identity(f) in identity(sentence) for f in found):
                continue
            found.append(sentence)
    return found


def pending(agent_id: int) -> list[dict]:
    return list(_load(agent_id)["items"])


def propose(agent_id: int, *, thread_id: int, title: str | None, turns: list[tuple[int, str]]) -> list[dict]:
    """Queue proposals from the person's messages (turn id, text) of one
    conversation. Returns the new proposals."""
    data = _load(agent_id)
    known = {identity(p["text"]) for p in data["items"]} | set(data["decided"])
    notes = identity(agent_memory.read_memory(agent_id).text)
    added: list[dict] = []
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    for turn_id, text in turns:
        for sentence in statements(text):
            clean, redacted = redact_secrets(sentence)
            key = identity(clean)
            if redacted or key in known or key in notes:
                continue
            if len(data["items"]) >= MAX_PENDING or len(added) >= MAX_PER_CONVERSATION:
                break
            label = " ".join((title or f"conversation {thread_id}").split())[:60].replace('"', "'")
            proposal = {
                "id": uuid4().hex,
                "at": time.time(),
                "text": clean,
                "line": f'- {day} · from "{label}" · The person said: "{clean}"',
                "target": agent_memory.MEMORY_FILE,
                "thread_id": thread_id,
                "thread_title": title,
                "turn_id": turn_id,
            }
            data["items"].append(proposal)
            known.add(key)
            added.append(proposal)
    _save(agent_id, data)
    return added


def mark_scanned(agent_id: int, thread_id: int, turn_id: int) -> None:
    data = _load(agent_id)
    data["scanned"][str(thread_id)] = turn_id
    _save(agent_id, data)


def scanned_through(agent_id: int, thread_id: int) -> int:
    value = _load(agent_id)["scanned"].get(str(thread_id))
    return value if isinstance(value, int) else 0


def _take(agent_id: int, proposal_id: str) -> tuple[dict, dict]:
    data = _load(agent_id)
    proposal = next((p for p in data["items"] if p.get("id") == proposal_id), None)
    if proposal is None:
        raise ProposalNotFound(proposal_id)
    data["items"] = [p for p in data["items"] if p.get("id") != proposal_id]
    data["decided"].append(identity(proposal["text"]))
    return data, proposal


def reject(agent_id: int, proposal_id: str) -> None:
    data, _ = _take(agent_id, proposal_id)
    _save(agent_id, data)


def accept(agent_id: int, proposal_id: str, *, line: str | None = None, target: str | None = None) -> dict:
    """Append the proposal (or the person's edited `line`) to MEMORY.md, or to
    `target` (a topic file name). Journaled. Returns {"path", "hash"}.
    Raises ProposalNotFound, InvalidTopicName, MemoryTooLarge."""
    data, proposal = _take(agent_id, proposal_id)
    text = " ".join((line if line is not None and line.strip() else proposal["line"]).split())
    name = target or proposal.get("target") or agent_memory.MEMORY_FILE
    if name == agent_memory.MEMORY_FILE:
        before = agent_memory.read_memory(agent_id).text
        after = f"{before.rstrip()}\n{text}\n" if before.strip() else f"{text}\n"
        doc, _ = agent_memory.write_memory(agent_id, after)
        memory_journal.record(agent_id, agent_memory.MEMORY_FILE, actor="proposal", before=before, after=doc.text)
        result = {"path": agent_memory.MEMORY_FILE, "hash": doc.hash}
    else:
        memory_topics.check_name(name)
        current = memory_topics.read_topic(agent_id, name)
        before = current.text if current.exists else None
        after = f"{current.text.rstrip()}\n{text}\n" if current.text.strip() else f"{text}\n"
        doc, _, _ = memory_topics.write_topic(agent_id, name, after)
        memory_journal.record(agent_id, memory_journal.note_path(name), actor="proposal", before=before, after=doc.text)
        result = {"path": memory_journal.note_path(name), "hash": doc.hash}
    _save(agent_id, data)
    return result


async def sweep(db, owner_id: str, agent_id: int, *, now: datetime | None = None) -> int:
    """Read the agent's conversations that have gone quiet since they were
    last read, and queue what they propose. Returns how many were added."""
    from sqlalchemy import select

    from app.db.models import Thread, Turn

    now = now or datetime.now(UTC)
    threads = (
        await db.execute(
            select(Thread)
            .where(Thread.owner_id == owner_id, Thread.agent_id == agent_id, Thread.archived_at.is_(None))
            .order_by(Thread.updated_at.desc(), Thread.id.desc())
            .limit(SCAN_THREADS)
        )
    ).scalars().all()
    added = 0
    for thread in threads:
        last = (
            await db.execute(
                select(Turn).where(Turn.thread_id == thread.id).order_by(Turn.seq.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if last is None or last.status == "running" or last.finished_at is None:
            continue
        finished = last.finished_at if last.finished_at.tzinfo else last.finished_at.replace(tzinfo=UTC)
        if (now - finished).total_seconds() < QUIET_SECONDS:
            continue
        through = scanned_through(agent_id, thread.id)
        if last.id <= through:
            continue
        rows = (
            await db.execute(
                select(Turn.id, Turn.prompt_text)
                .where(Turn.thread_id == thread.id, Turn.id > through)
                .order_by(Turn.seq)
            )
        ).all()
        turns = [(turn_id, text or "") for turn_id, text in rows]
        added += len(propose(agent_id, thread_id=thread.id, title=thread.title, turns=turns))
        mark_scanned(agent_id, thread.id, last.id)
    return added
