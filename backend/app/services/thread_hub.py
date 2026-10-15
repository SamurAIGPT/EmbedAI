"""Live, in-memory signals for durable threads.

The database is the only source of order and of stored events. This hub never
carries a stored event: it only (1) wakes the streams of a thread so they read
the database now instead of at their next timer, and (2) carries the typing
deltas of the message in progress, which are display-only, unnumbered, and
never stored. A lost or late wake costs at most one poll interval; a dropped
delta is superseded by the completed `assistant.message`.

Each delta (and the in-progress snapshot) is tagged with `after_seq`: the
sequence number of the last event the run had stored when the message started.
The message will be stored with a higher seq, so a stream whose cursor is past
`after_seq` may already have delivered it and drops the typing text instead of
showing it again. `None` means the run had stored nothing yet, so nothing can
be stale.

Everything here runs on the event loop thread and never awaits while it holds
state, so "take the text so far and start listening" is atomic: a new
subscriber can neither miss nor repeat a delta.
"""

import asyncio
from dataclasses import dataclass, field

SUBSCRIBER_QUEUE_SIZE = 256


@dataclass
class Delta:
    turn_id: int
    text: str
    after_seq: int | None = None


@dataclass(eq=False)
class ThreadSubscription:
    thread_id: int
    # The text of the message in progress when the subscription started, to be
    # sent once as a `replace` snapshot, then live deltas.
    snapshot: str
    snapshot_turn_id: int | None = None
    snapshot_after_seq: int | None = None
    # Set when something was committed: read the database. Cleared by the reader
    # just before it reads.
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    # Set by a commit or by typing text: the reader has something to do. A delta
    # sets only this, so typing shows up at once without a database read each time.
    signal: asyncio.Event = field(default_factory=asyncio.Event)
    deltas: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE))
    lagged: bool = False
    # The thread was deleted: the stream ends instead of waiting for events that
    # will never come.
    closed: bool = False

    async def wait(self, timeout: float) -> bool:
        """Wait until there is something to do (a commit or typing text) or
        `timeout` seconds. True if woken. The caller reads the database when
        `wake` is set or the wait timed out, and always sends pending typing."""
        try:
            await asyncio.wait_for(self.signal.wait(), timeout)
        except TimeoutError:
            return False
        self.signal.clear()
        return True

    def drain_deltas(self) -> list[Delta]:
        items = []
        while not self.deltas.empty():
            items.append(self.deltas.get_nowait())
        return items


@dataclass
class _Live:
    # Text of the message in progress, so a late subscriber can be given what
    # was typed so far.
    in_progress: str = ""
    in_progress_turn: int | None = None
    in_progress_after: int | None = None
    subscribers: set = field(default_factory=set)


class ThreadHub:
    def __init__(self) -> None:
        self._threads: dict[int, _Live] = {}

    def _live(self, thread_id: int) -> _Live:
        return self._threads.setdefault(thread_id, _Live())

    def subscribe(self, thread_id: int) -> ThreadSubscription:
        live = self._live(thread_id)
        subscription = ThreadSubscription(
            thread_id=thread_id,
            snapshot=live.in_progress,
            snapshot_turn_id=live.in_progress_turn,
            snapshot_after_seq=live.in_progress_after,
        )
        live.subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: ThreadSubscription) -> None:
        live = self._threads.get(subscription.thread_id)
        if live is not None:
            live.subscribers.discard(subscription)
            if not live.subscribers and not live.in_progress:
                self._threads.pop(subscription.thread_id, None)

    def subscriber_count(self, thread_id: int) -> int:
        live = self._threads.get(thread_id)
        return len(live.subscribers) if live else 0

    def notify(self, thread_id: int) -> None:
        """Something new was committed for this thread: read the database."""
        live = self._threads.get(thread_id)
        if live is None:
            return
        for subscriber in live.subscribers:
            subscriber.wake.set()
            subscriber.signal.set()

    def close(self, thread_id: int) -> None:
        """The thread was deleted: end every stream listening to it."""
        live = self._threads.pop(thread_id, None)
        if live is None:
            return
        for subscriber in live.subscribers:
            subscriber.closed = True
            subscriber.wake.set()
            subscriber.signal.set()

    def delta(self, thread_id: int, turn_id: int, text: str, after_seq: int | None = None) -> None:
        live = self._live(thread_id)
        if not live.in_progress:  # the first text of a new message
            live.in_progress_turn = turn_id
            live.in_progress_after = after_seq
        live.in_progress += text
        for subscriber in list(live.subscribers):
            try:
                subscriber.deltas.put_nowait(Delta(turn_id, text, after_seq))
                subscriber.signal.set()  # typing is sent at once; no database read needed
            except asyncio.QueueFull:
                # A slow reader never holds a run back. It misses typing deltas
                # and is told, so it can show the stored message when it lands.
                subscriber.lagged = True

    def message_done(self, thread_id: int) -> None:
        """The message in progress was stored (or abandoned): nothing is typing."""
        live = self._threads.get(thread_id)
        if live is not None:
            live.in_progress = ""
            live.in_progress_turn = None
            live.in_progress_after = None
            if not live.subscribers:
                self._threads.pop(thread_id, None)


hub = ThreadHub()
