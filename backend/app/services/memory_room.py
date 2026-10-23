"""Learning from group chats (docs/MEMORY.md, "Learn from group chats").

With the switch on (`memory_settings.learn_from_rooms`, off by default, one
switch for every agent), a member agent's room turns feed its memory capture the
same way a one-to-one chat does, batched per agent and room. What the agent read
since it last spoke in the room is read with its reply, under room rules:

* the person's own lines count as theirs;
* another person's lines give only facts about the shared work, and never reach
  About me (a batch holding them adds nothing there);
* another agent's lines give only an outcome it verified or a decision the person
  agreed to, never its suggestions or claims.

A stretch with no person's words is skipped. The conversation key is the room's
own thread, so entries are sourced to the room (`from room "Launch" (noticed)`).
The switch, the room and the agent's membership are checked again when the batch
is captured. Nothing else about a room reaches memory: its other members' notes,
dispatches and the shared instructions are never read.
"""

from sqlalchemy import select

from app.db.models import Event, Room, RoomMember
from app.services import memory_settings
from app.services.memory_capture import RoomLine, Turn

MAX_LINES = 12
NAME_MAX = 40


def _clean_name(label) -> str:
    return " ".join(str(label or "").replace(":", " ").split())[:NAME_MAX] or "Someone"


def lines_for(events: list[Event], agent_id: int, reply: Event) -> list[RoomLine]:
    """The lines an agent read between its last message in the room and `reply`
    (events in order), at most MAX_LINES, newest kept."""
    before = [e for e in events if e.seq < reply.seq]
    last = max(
        (
            e.seq for e in before
            if (e.data or {}).get("author", {}).get("kind") == "agent" and e.data["author"].get("agent_id") == agent_id
        ),
        default=0,
    )
    out: list[RoomLine] = []
    for event in before:
        data = event.data or {}
        author = data.get("author") or {}
        text = data.get("text")
        if event.seq <= last or not isinstance(text, str) or not text.strip():
            continue
        if author.get("kind") == "person":
            # The app has one owner: their lines are "You". A line marked as not the owner's is another person's.
            if author.get("owner") is False:
                out.append(RoomLine("other", _clean_name(author.get("label")), text))
            else:
                out.append(RoomLine("person", "Person", text))
        elif author.get("kind") == "agent" and author.get("agent_id") != agent_id:
            out.append(RoomLine("peer", _clean_name(author.get("label")), text))
    return out[-MAX_LINES:]


async def live_room(db, owner_id: str, room_thread_id: int, agent_id: int) -> Room | None:
    """The room, when the switch is on, the room still exists and the agent is
    still a member; else None."""
    if not memory_settings.learn_from_rooms():
        return None
    room = await db.scalar(select(Room).where(Room.thread_id == room_thread_id, Room.owner_id == owner_id))
    if room is None or room.deleting_at is not None or room.archived_at is not None:
        return None
    member = await db.scalar(select(RoomMember).where(RoomMember.room_id == room.id, RoomMember.agent_id == agent_id))
    return room if member is not None else None


async def turn_for(db, room: Room, agent_id: int, turn_id: int) -> Turn | None:
    """What one of the agent's room turns read and answered, or None when it
    left no reply or read no person's words."""
    events = list(
        (await db.execute(select(Event).where(Event.thread_id == room.thread_id, Event.type == "room.message").order_by(Event.seq))).scalars()
    )
    reply = next(
        (
            e for e in reversed(events)
            if (e.data or {}).get("author", {}).get("kind") == "agent"
            and e.data["author"].get("agent_id") == agent_id
            and ((e.data.get("source") or {}).get("turn_id") == turn_id)
        ),
        None,
    )
    if reply is None:
        return None
    read = lines_for(events, agent_id, reply)
    if not any(line.by != "peer" for line in read):
        return None
    return Turn(
        turn_id,
        "\n".join(line.text for line in read if line.by == "person"),
        reply.data.get("text") or "",
        owner=not any(line.by == "other" for line in read),
        room=tuple(read),
    )
