"""The SSE stream of a thread (F04, slice 3).

The generator is driven directly against the real database and a real hub: an
endless stream cannot be read through the in-process HTTP test transport, which
buffers a whole response. Events are written by real committed transactions from
separate connections."""

import asyncio
import json

import pytest

from app.services import thread_store as store
from app.services import thread_stream
from app.services.thread_hub import ThreadHub
from app.services.thread_stream import event_frames
from tests.services.thread_harness import (  # noqa: F401
    cleanup,
    new_thread,
    owner,
    sessions,
    wait_until,
)

FAST = dict(poll_seconds=0.05, keepalive_seconds=60)


class Stream:
    """Reads a stream in the background, like a connected browser."""

    def __init__(self, thread_id, sessions, hub, *, cursor=0, **options):
        options = {**FAST, **options}
        self.frames: list[str] = []
        self.hub = hub
        self.task = asyncio.create_task(
            self._read(event_frames(thread_id, cursor=cursor, hub=hub, session_factory=sessions, **options))
        )

    async def _read(self, generator):
        try:
            async for frame in generator:
                self.frames.append(frame)
        finally:
            await generator.aclose()

    @property
    def stored(self) -> list[dict]:
        """The stored events received, parsed (frames that carry an id)."""
        return [parse(f) for f in self.frames if f.startswith("id: ")]

    @property
    def typing(self) -> list[dict]:
        return [parse(f) for f in self.frames if f.startswith("event: text.delta")]

    @property
    def seqs(self) -> list[int]:
        return [e["seq"] for e in self.stored]

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


def parse(frame: str) -> dict:
    fields = dict(line.split(": ", 1) for line in frame.strip().split("\n"))
    data = json.loads(fields["data"])
    return {"id": fields.get("id"), "event": fields["event"], **data}


async def append(sessions, thread_id, n=1, type_="note", **data):
    for _ in range(n):
        async with sessions() as db:
            await store.append_event(db, thread_id, type_, data or {})


async def wait_for_seqs(stream, count):
    await wait_until(lambda: len(stream.seqs) >= count)


@pytest.fixture
def hub():
    return ThreadHub()


async def test_stored_events_arrive_in_order_with_their_seq_as_the_frame_id(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    await append(sessions, thread.id, 3)
    stream = Stream(thread.id, sessions, hub)
    await wait_for_seqs(stream, 3)
    assert stream.seqs == [1, 2, 3]
    assert [e["id"] for e in stream.stored] == ["1", "2", "3"]
    first = stream.stored[0]
    assert (first["thread_id"], first["type"], first["event"], first["turn_id"]) == (thread.id, "note", "note", None)
    await stream.stop()


async def test_a_reconnect_with_the_last_id_returns_exactly_the_missed_events(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    await append(sessions, thread.id, 3)
    first = Stream(thread.id, sessions, hub)
    await wait_for_seqs(first, 3)
    await first.stop()  # the client goes away having seen event 3

    await append(sessions, thread.id, 4)  # events 4..7 happen meanwhile
    second = Stream(thread.id, sessions, hub, cursor=max(first.seqs))
    await wait_for_seqs(second, 4)
    assert second.seqs == [4, 5, 6, 7]  # exactly the missed ones, none repeated
    await asyncio.sleep(0.3)
    assert second.seqs == [4, 5, 6, 7]
    await second.stop()


async def test_events_written_while_streaming_are_each_delivered_exactly_once_in_order(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    stream = Stream(thread.id, sessions, hub)

    async def writer(n):
        for i in range(5):
            await append(sessions, thread.id, 1, writer=n, i=i)
            hub.notify(thread.id)

    await asyncio.gather(*(writer(n) for n in range(4)))
    await wait_for_seqs(stream, 20)
    await asyncio.sleep(0.3)
    assert stream.seqs == list(range(1, 21))  # gap-free, ascending, no duplicates
    await stream.stop()


async def test_wakes_that_are_missing_early_or_repeated_change_nothing(sessions, hub, owner):
    """The hub only says "read now". With no wakes at all the poll finds the events; with a
    storm of spurious wakes nothing is sent twice."""
    thread = await new_thread(sessions, owner)
    stream = Stream(thread.id, sessions, hub, poll_seconds=0.1)
    await append(sessions, thread.id, 2)  # committed, never announced
    await wait_for_seqs(stream, 2)
    for _ in range(50):
        hub.notify(thread.id)
    await append(sessions, thread.id, 1)
    for _ in range(50):
        hub.notify(thread.id)
    await wait_for_seqs(stream, 3)
    await asyncio.sleep(0.3)
    assert stream.seqs == [1, 2, 3]
    await stream.stop()


async def test_a_backlog_larger_than_a_page_is_read_in_pages_without_gaps(sessions, hub, owner, monkeypatch):
    monkeypatch.setattr(thread_stream, "PAGE_SIZE", 3)
    thread = await new_thread(sessions, owner)
    await append(sessions, thread.id, 10)
    stream = Stream(thread.id, sessions, hub)
    await wait_for_seqs(stream, 10)
    await asyncio.sleep(0.2)
    assert stream.seqs == list(range(1, 11))
    await stream.stop()


async def test_the_stream_holds_no_database_session_while_a_client_is_slow(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    await append(sessions, thread.id, 3)
    open_sessions = 0
    seen_while_yielding = []

    class Counting:
        def __call__(self):
            return self

        async def __aenter__(self):
            nonlocal open_sessions
            open_sessions += 1
            self.inner = sessions()
            return await self.inner.__aenter__()

        async def __aexit__(self, *exc):
            nonlocal open_sessions
            open_sessions -= 1
            return await self.inner.__aexit__(*exc)

    generator = event_frames(thread.id, cursor=0, hub=hub, session_factory=Counting(), **FAST)
    for _ in range(3):
        await generator.__anext__()
        seen_while_yielding.append(open_sessions)  # the client is "reading" this frame
    await generator.aclose()
    assert seen_while_yielding == [0, 0, 0]


async def test_closing_the_stream_unsubscribes_from_the_hub(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    stream = Stream(thread.id, sessions, hub)
    await wait_until(lambda: hub.subscriber_count(thread.id) == 1)
    await stream.stop()
    assert hub.subscriber_count(thread.id) == 0


async def test_an_idle_stream_sends_keepalive_comments(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    stream = Stream(thread.id, sessions, hub, poll_seconds=0.05, keepalive_seconds=0.2)
    await wait_until(lambda: any(f == ": keepalive\n\n" for f in stream.frames))
    assert stream.stored == []
    await stream.stop()


# --- typing previews -------------------------------------------------------------------------


async def test_typing_has_no_id_and_never_moves_the_cursor(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    stream = Stream(thread.id, sessions, hub)
    await wait_until(lambda: hub.subscriber_count(thread.id) == 1)
    hub.delta(thread.id, 7, "Hel", after_seq=None)
    hub.delta(thread.id, 7, "lo", after_seq=None)
    hub.notify(thread.id)
    await wait_until(lambda: len(stream.typing) == 2)
    assert [t["data"] for t in stream.typing] == [
        {"text": "Hel", "mode": "append"}, {"text": "lo", "mode": "append"}
    ]
    assert not any(f.startswith("id: ") for f in stream.frames if "text.delta" in f)
    assert stream.typing[0]["turn_id"] == 7
    await stream.stop()


async def test_typing_is_dropped_once_the_stored_message_that_covers_it_was_sent(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    await append(sessions, thread.id, 2)  # seq 1, 2 are stored: the message will be seq 3
    stream = Stream(thread.id, sessions, hub)
    await wait_for_seqs(stream, 2)

    hub.delta(thread.id, 1, "typing for the next message", after_seq=2)  # started after seq 2
    await append(sessions, thread.id, 1, type_="assistant.message", text="typing for the next message")
    hub.notify(thread.id)
    await wait_for_seqs(stream, 3)
    # The stream may send the preview before the message arrives, but never after.
    last_typing = max((i for i, f in enumerate(stream.frames) if "text.delta" in f), default=-1)
    message_at = next(i for i, f in enumerate(stream.frames) if f.startswith("id: 3"))
    assert last_typing < message_at

    # A delta that was typed before the cursor moved on, delivered late, is not shown.
    hub.delta(thread.id, 1, "stale", after_seq=2)  # message that started after seq 2: already stored as 3
    hub.notify(thread.id)
    await asyncio.sleep(0.3)
    assert not any("stale" in f for f in stream.frames)

    hub.delta(thread.id, 1, "next message", after_seq=3)  # a new message after seq 3 is relevant
    hub.notify(thread.id)
    await wait_until(lambda: any("next message" in f for f in stream.frames))
    await stream.stop()


async def test_a_listener_joining_mid_message_gets_a_replace_snapshot_first(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    await append(sessions, thread.id, 2)
    hub.delta(thread.id, 4, "typed so ", after_seq=2)
    hub.delta(thread.id, 4, "far", after_seq=2)
    stream = Stream(thread.id, sessions, hub)
    await wait_until(lambda: len(stream.typing) == 1)
    assert stream.typing[0]["data"] == {"text": "typed so far", "mode": "replace"}
    # and it came after the stored events it covers up to
    assert stream.frames.index(next(f for f in stream.frames if "text.delta" in f)) == 2
    await stream.stop()


async def test_a_snapshot_for_a_message_the_client_already_has_is_not_sent(sessions, hub, owner):
    thread = await new_thread(sessions, owner)
    await append(sessions, thread.id, 3)
    hub.delta(thread.id, 4, "already stored as seq 3", after_seq=2)  # message began after 2; client has 3
    stream = Stream(thread.id, sessions, hub, cursor=3)
    await asyncio.sleep(0.3)
    assert stream.typing == []
    await stream.stop()


async def test_a_slow_client_is_told_typing_was_dropped(sessions, hub, owner, monkeypatch):
    from app.services import thread_hub

    monkeypatch.setattr(thread_hub, "SUBSCRIBER_QUEUE_SIZE", 2)  # read when a subscription is made
    thread = await new_thread(sessions, owner)
    stream = Stream(thread.id, sessions, hub)
    await wait_until(lambda: hub.subscriber_count(thread.id) == 1)
    # Published in one go (no await between), far faster than the reader drains.
    for i in range(50):
        hub.delta(thread.id, 1, f"y{i}", after_seq=None)
    hub.notify(thread.id)
    await wait_until(lambda: any(f.startswith("event: runtime.status") for f in stream.frames))
    status = parse(next(f for f in stream.frames if f.startswith("event: runtime.status")))
    assert status["data"] == {"state": "lagged"}
    assert len(stream.typing) <= 2  # what fitted; the rest was dropped, never blocking the writer
    await stream.stop()


async def test_typing_is_sent_at_once_and_without_a_database_read(sessions, hub, owner):
    """Typing must not wait for the poll (here 30 s) and must not cost a read per chunk:
    only a commit, or the poll, reads the database."""
    thread = await new_thread(sessions, owner)
    reads = 0

    class Counting:
        def __call__(self):
            return self

        async def __aenter__(self):
            nonlocal reads
            reads += 1
            self.inner = sessions()
            return await self.inner.__aenter__()

        async def __aexit__(self, *exc):
            return await self.inner.__aexit__(*exc)

    stream = Stream(thread.id, Counting(), hub, poll_seconds=30)
    await wait_until(lambda: hub.subscriber_count(thread.id) == 1 and reads >= 1)
    await asyncio.sleep(0.1)
    reads_before = reads

    started = asyncio.get_running_loop().time()
    for chunk in ("Hel", "lo ", "there"):
        hub.delta(thread.id, 1, chunk, after_seq=None)  # no notify: nothing was committed
    await wait_until(lambda: len(stream.typing) >= 1, timeout=3)
    assert asyncio.get_running_loop().time() - started < 3  # not the 30 s poll
    await wait_until(lambda: "".join(t["data"]["text"] for t in stream.typing) == "Hello there", timeout=3)
    assert reads == reads_before  # typing cost no database read

    await append(sessions, thread.id, 1)
    hub.notify(thread.id)  # a commit does
    await wait_for_seqs(stream, 1)
    assert reads > reads_before
    await stream.stop()
