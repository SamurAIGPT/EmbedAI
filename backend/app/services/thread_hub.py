"""Live, in-memory signals for durable threads.

The database is the only source of order and of stored events. This hub never
carries a stored event: it only (1) wakes the streams of a thread so they read
the database now instead of at their next timer, and (2) carries the typing
deltas of the message in progress, which are display-only, unnumbered, and
never stored. A lost or late wake costs at most one poll interval; a dropped
delta is superseded by the completed `assistant.message`.

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


@dataclass(eq=False)
class ThreadSubscription:
    thread_id: int
    # The text of the message in progress when the subscription started, to be
    # sent once as a `replace` snapshot, then live deltas.
    snapshot: str
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    deltas: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE))
    lagged: bool = False

    async def wait(self, timeout: float) -> bool:
        """Wait for a wake or `timeout` seconds. True if woken. Either way the
        caller then reads the database from its cursor."""
        try:
            await asyncio.wait_for(self.wake.wait(), timeout)
        except TimeoutError:
            return False
        self.wake.clear()
        return True

    def drain_deltas(self) -> list[Delta]:
        items = []
        while not self.deltas.empty():
            items.append(self.deltas.get_nowait())
        return items


@dataclass
class _Live:
    # Text of the message in progress, per turn, so a late subscriber can be
    # given what was typed so far.
    in_progress: str = ""
    subscribers: set = field(default_factory=set)


class ThreadHub:
    def __init__(self) -> None:
        self._threads: dict[int, _Live] = {}

    def _live(self, thread_id: int) -> _Live:
        return self._threads.setdefault(thread_id, _Live())

    def subscribe(self, thread_id: int) -> ThreadSubscription:
        live = self._live(thread_id)
        subscription = ThreadSubscription(thread_id=thread_id, snapshot=live.in_progress)
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

    def delta(self, thread_id: int, turn_id: int, text: str) -> None:
        live = self._live(thread_id)
        live.in_progress += text
        for subscriber in list(live.subscribers):
            try:
                subscriber.deltas.put_nowait(Delta(turn_id, text))
            except asyncio.QueueFull:
                # A slow reader never holds a run back. It misses typing deltas
                # and is told, so it can show the stored message when it lands.
                subscriber.lagged = True

    def message_done(self, thread_id: int) -> None:
        """The message in progress was stored (or abandoned): nothing is typing."""
        live = self._threads.get(thread_id)
        if live is not None:
            live.in_progress = ""
            if not live.subscribers:
                self._threads.pop(thread_id, None)


hub = ThreadHub()
