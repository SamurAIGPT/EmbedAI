"""The server-sent-events stream of one thread.

The database is the only source of order. The stream keeps a cursor (the seq of
the last stored event it sent) that only moves forward, and repeatedly reads the
stored events after it, in ascending order, from the database. The live hub only
wakes it, so a wake that arrives late, twice, out of order or never changes
nothing a client receives: a lost wake costs at most one poll interval. Typing
text also wakes the stream, so a preview is sent at once, but without a database
read: only a commit (or the poll) triggers one.

That is what makes `Last-Event-ID` exact. A client that saw event 11 has been
sent event 10; reconnecting with 11 returns everything after it and nothing
twice. There is no per-connection buffer of stored events, so a slow client
simply reads later.

Typing text (`text.delta`) is separate: display-only, unnumbered (no `id:` line,
so it never moves `Last-Event-ID`), never stored. Each delta is dropped if the
cursor has moved past the point where its message started, because the stored
`assistant.message` then covers it.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.services import thread_store as store
from app.services.thread_hub import ThreadHub

POLL_SECONDS = 1.0
KEEPALIVE_SECONDS = 15.0
PAGE_SIZE = 500

KEEPALIVE_FRAME = ": keepalive\n\n"


def _json(envelope: dict) -> str:
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)


def format_event(event) -> str:
    """One stored event. `id:` is its seq, so a reconnecting client resumes."""
    envelope = {
        "thread_id": event.thread_id,
        "seq": event.seq,
        "type": event.type,
        "turn_id": event.turn_id,
        "data": event.data,
        "ts": event.created_at.isoformat(),
    }
    return f"id: {event.seq}\nevent: {event.type}\ndata: {_json(envelope)}\n\n"


def format_typing(thread_id: int, turn_id: int | None, text: str, mode: str) -> str:
    """Live typing text. No `id:`: it is not a stored event."""
    envelope = {
        "thread_id": thread_id,
        "type": "text.delta",
        "turn_id": turn_id,
        "data": {"text": text, "mode": mode},
    }
    return f"event: text.delta\ndata: {_json(envelope)}\n\n"


def format_status(thread_id: int, state: str) -> str:
    envelope = {"thread_id": thread_id, "type": "runtime.status", "data": {"state": state}}
    return f"event: runtime.status\ndata: {_json(envelope)}\n\n"


def _still_relevant(after_seq: int | None, cursor: int) -> bool:
    """Typing text for a message that started after `after_seq`. Once the cursor
    is past that point the message (or something after it) was already sent, and
    the stored message supersedes the preview."""
    return after_seq is None or after_seq >= cursor


async def event_frames(
    thread_id: int,
    *,
    cursor: int,
    hub: ThreadHub,
    session_factory: Callable[[], Any],
    poll_seconds: float = POLL_SECONDS,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
) -> AsyncIterator[str]:
    """SSE frames for one thread, forever (until the client goes away: the
    generator is closed or cancelled and unsubscribes). `cursor` is the seq of
    the last event the client already has (0 for none)."""
    loop = asyncio.get_running_loop()
    subscription = hub.subscribe(thread_id)  # before the first read: no gap
    snapshot_pending = bool(subscription.snapshot)
    last_sent = loop.time()
    read_stored = True  # the first pass always reads
    try:
        while True:
            # 1. Stored events after the cursor, ascending. Each read is its own
            # short session, closed before anything is yielded, so a slow client
            # never holds a database transaction open. A pass woken only by
            # typing text skips the read.
            if read_stored:
                subscription.wake.clear()  # before reading: a commit during the read re-sets it
                while True:
                    async with session_factory() as db:
                        events = await store.list_events(db, thread_id, after=cursor, limit=PAGE_SIZE)
                    for event in events:
                        yield format_event(event)
                        cursor = event.seq
                    if events:
                        last_sent = loop.time()
                    if len(events) < PAGE_SIZE:
                        break

            # 2. Typing text, judged against the cursor as it now stands.
            if snapshot_pending:
                snapshot_pending = False
                if _still_relevant(subscription.snapshot_after_seq, cursor):
                    yield format_typing(thread_id, subscription.snapshot_turn_id, subscription.snapshot, "replace")
                    last_sent = loop.time()
            for delta in subscription.drain_deltas():
                if _still_relevant(delta.after_seq, cursor):
                    yield format_typing(thread_id, delta.turn_id, delta.text, "append")
                    last_sent = loop.time()
            if subscription.lagged:
                subscription.lagged = False
                yield format_status(thread_id, "lagged")  # some typing was dropped; the stored message will land

            # 3. Wait for a commit or typing text, or poll anyway.
            woke = await subscription.wait(poll_seconds)
            read_stored = (not woke) or subscription.wake.is_set()
            if not woke and loop.time() - last_sent >= keepalive_seconds:
                yield KEEPALIVE_FRAME
                last_sent = loop.time()
    finally:
        hub.unsubscribe(subscription)
