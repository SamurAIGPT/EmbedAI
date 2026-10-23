"""Past-conversation search for an agent (docs/MEMORY.md).

`session_search` lets an agent look through its OWN earlier conversations, the
rooms it is a member of and its memory files, by words, by time, or both:
"what happened since yesterday's standup" needs no keyword. `session_read`
returns one whole message from a hit.

Scope, and what never appears:
* only this agent's conversations (its one-to-one chats) and the room
  timelines of rooms it belongs to; never another
  agent's private history; archived conversations and delegated-task threads
  are left out;
* memory files: MEMORY.md and topic notes without expired or superseded
  entries, plus `archive.md` and the daily logs, which stay searchable on
  purpose (older entries are never lost to the agent);
* a hit from a one-to-one chat that is used in a ROOM turn is marked
  `private`, and the runtime tells the room (a notice) once per chat: a private
  chat reaches a room only openly.

Words must all match (case-insensitive, one portable query on SQLite and
PostgreSQL). `since` and `until` take a span back from now ("24h", "3d",
"2w"), "today", "yesterday", or a date; a form naming a day means its start
for `since` and its last instant for `until`.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select

from app.db.models import Event, Room, RoomMember, Thread
from app.services import agent_memory, conversation_search, memory_entries, memory_log, memory_recall, memory_topics

MAX_LIMIT = 25
DEFAULT_LIMIT = 12
READ_MAX_CHARS = 8_000
SNIPPET = (100, 200)
_SPAN = re.compile(r"^(\d{1,4})\s*([hdw])$")
_WHOLE_DAY = re.compile(r"^(?:today|yesterday|\d{4}-\d{2}-\d{2})$")
_TYPES = {"turn.started": "user", "assistant.message": "assistant", "room.message": "assistant"}


class BadTime(ValueError):
    pass


def parse_since(value: str, now: datetime | None = None) -> datetime | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    now = now or datetime.now(UTC)
    span = _SPAN.match(text)
    if span:
        unit = {"h": timedelta(hours=1), "d": timedelta(days=1), "w": timedelta(weeks=1)}[span.group(2)]
        return now - int(span.group(1)) * unit
    if text in ("today", "yesterday"):
        start = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        return (start - timedelta(days=1) if text == "yesterday" else start).astimezone(UTC)
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            return datetime.strptime(text, "%Y-%m-%d").astimezone().astimezone(UTC)
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise BadTime(value) from None
    return (parsed if parsed.tzinfo else parsed.astimezone()).astimezone(UTC)


def parse_until(value: str, now: datetime | None = None) -> datetime | None:
    at = parse_since(value, now)
    if at is None or not _WHOLE_DAY.match(value.strip().lower()):
        return at
    return (at.astimezone().replace(hour=23, minute=59, second=59, microsecond=999000)).astimezone(UTC)


@dataclass
class Hit:
    thread_id: int
    seq: int | None
    at: datetime | None
    speaker: str
    where: str
    snippet: str
    current: bool = False
    private: bool = False


@dataclass
class MemoryHit:
    path: str
    snippet: str


@dataclass
class Result:
    conversations: list[Hit] = field(default_factory=list)
    memory: list[MemoryHit] = field(default_factory=list)


async def _scope(db, owner_id: str, agent_id: int):
    """(own thread rows by id, room thread id -> room name) for this agent."""
    rooms = {
        r.thread_id: r.name
        for r in (
            await db.execute(
                select(Room)
                .join(RoomMember, RoomMember.room_id == Room.id)
                .where(Room.owner_id == owner_id, RoomMember.agent_id == agent_id, Room.archived_at.is_(None))
            )
        ).scalars()
    }
    return rooms


def _where(thread: Thread, rooms: dict[int, str], room_names: dict[int, str]) -> tuple[str, bool]:
    if thread.id in rooms:
        return f'room "{rooms[thread.id]}"', False
    if thread.kind == "room_member":
        return f'room "{room_names.get(thread.room_id, "room")}", your replies', False
    title = f' "{" ".join((thread.title or "").split())[:60]}"' if thread.title else ""
    return f"1:1 chat{title}", True


async def search(
    db,
    *,
    owner_id: str,
    agent_id: int,
    current_thread_id: int | None,
    query: str = "",
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
    scope: str = "all",
    memory: bool = True,
) -> Result:
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    terms = [t for t in conversation_search.query_terms(query) if t not in memory_recall.STOPWORDS and len(t) >= 2]
    result = Result()
    if scope in ("all", "conversations"):
        result.conversations = await _conversations(
            db, owner_id, agent_id, current_thread_id, terms, since, until, limit
        )
    if scope in ("all", "memory") and memory and terms:
        result.memory = memory_hits(agent_id, terms, since, until, limit)
    return result


async def _conversations(db, owner_id, agent_id, current_thread_id, terms, since, until, limit) -> list[Hit]:
    rooms = await _scope(db, owner_id, agent_id)
    message_text = func.coalesce(Event.data["text"].as_string(), Event.data["prompt_text"].as_string())
    # A room's own timeline carries what was said there, so the member threads
    # (which replay the room window) are left out: no duplicates.
    own = and_(Thread.agent_id == agent_id, Thread.kind == "chat")
    in_scope = or_(own, Thread.id.in_(list(rooms))) if rooms else own
    statement = (
        select(Event, Thread, message_text.label("body"))
        .join(Thread, Thread.id == Event.thread_id)
        .where(Thread.owner_id == owner_id, Thread.archived_at.is_(None), in_scope, Event.type.in_(tuple(_TYPES)))
    )
    if terms:
        statement = statement.where(and_(*[message_text.icontains(t, autoescape=True) for t in terms]))
    if since is not None:
        statement = statement.where(Event.created_at >= since)
    if until is not None:
        statement = statement.where(Event.created_at <= until)
    rows = (await db.execute(statement.order_by(Event.created_at.desc(), Event.id.desc()).limit(limit * 4))).all()
    room_ids = {t.room_id for _, t, _ in rows if t.room_id is not None}
    room_names: dict[int, str] = {}
    if room_ids:
        room_names = {r.id: r.name for r in (await db.execute(select(Room).where(Room.id.in_(room_ids)))).scalars()}
    out: list[Hit] = []
    for event, thread, body in rows:
        body = body or ""
        if terms and not conversation_search._matches_all(body, terms):
            continue
        if not body.strip():
            continue
        where, private = _where(thread, rooms, room_names)
        if event.type == "room.message":
            author = (event.data or {}).get("author") or {}
            speaker = "you" if author.get("agent_id") == agent_id else (author.get("label") or "someone")
        else:
            speaker = "the person" if event.type == "turn.started" else "you"
        snippet = (
            conversation_search.make_snippet(body, terms, before=SNIPPET[0], after=SNIPPET[1])[0]
            if terms
            else _head(body)
        )
        out.append(
            Hit(thread.id, event.seq, event.created_at, speaker, where, snippet,
                current=thread.id == current_thread_id, private=private)
        )
        if len(out) >= limit:
            break
    return out


def _head(text: str, limit: int = 220) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


async def read(db, *, owner_id: str, agent_id: int, thread_id: int, seq: int) -> dict | None:
    """One whole message from this agent's own conversations, or None when it
    is not one of them (another agent's id reads as missing)."""
    rooms = await _scope(db, owner_id, agent_id)
    thread = await db.get(Thread, thread_id)
    if (
        thread is None
        or thread.owner_id != owner_id
        or thread.archived_at is not None
        or not (thread.id in rooms or (thread.agent_id == agent_id and thread.kind == "chat"))
    ):
        return None
    event = (
        await db.execute(select(Event).where(Event.thread_id == thread_id, Event.seq == seq, Event.type.in_(tuple(_TYPES))))
    ).scalar_one_or_none()
    if event is None:
        return None
    data = event.data or {}
    text = data.get("text") if event.type != "turn.started" else data.get("prompt_text")
    if not isinstance(text, str) or not text.strip():
        return None
    room_names = {}
    if thread.room_id is not None:
        room = await db.get(Room, thread.room_id)
        room_names = {thread.room_id: room.name} if room else {}
    where, private = _where(thread, rooms, room_names)
    text, _ = agent_memory.redact_secrets(text)
    return {
        "thread_id": thread_id, "seq": seq, "at": event.created_at, "where": where, "private": private,
        "speaker": "the person" if event.type == "turn.started" else "you",
        "text": text if len(text) <= READ_MAX_CHARS else text[:READ_MAX_CHARS] + "…",
    }


# --- memory files ---------------------------------------------------------------------------


def _files(agent_id: int):
    """(path, text, searchable?) for MEMORY.md, topic notes and logs."""
    today = memory_entries.today()
    notes = agent_memory.read_memory(agent_id).text
    yield "MEMORY.md", memory_entries.without_superseded(memory_entries.without_expired(notes, today)[0]), None
    for topic in memory_topics.list_topics(agent_id):
        text = memory_topics.read_topic(agent_id, topic.name).text
        if topic.name != memory_topics.ARCHIVE_NAME:
            text = memory_entries.without_superseded(memory_entries.without_expired(text, today)[0])
        yield f"memory/{topic.name}", text, None
    for item in memory_log.list_logs(agent_id):
        yield f"memory/log/{item['day']}.md", memory_log.read_log(agent_id, item["day"]) or "", item["day"]


def memory_hits(agent_id: int, terms: list[str], since, until, limit: int) -> list[MemoryHit]:
    out: list[MemoryHit] = []
    for path, text, day in _files(agent_id):
        if day is not None:
            at = datetime.strptime(day, "%Y-%m-%d").astimezone()
            if (since is not None and at.astimezone(UTC) + timedelta(days=1) <= since) or (
                until is not None and at.astimezone(UTC) > until
            ):
                continue
        text, _ = agent_memory.redact_secrets(text)
        lines = [line for line in text.splitlines() if line.strip() and conversation_search._matches_all(line, terms)]
        for line in lines[:2]:
            out.append(MemoryHit(path, _head(line, 240)))
            if len(out) >= limit:
                return out
    return out
