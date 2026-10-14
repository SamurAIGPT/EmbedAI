"""In-memory live events for local runs (F02).

Events are ordered per run (`seq` from 1) and only held while a run is live
plus a short linger afterwards. Nothing is persisted: after a restart, or on a
different worker, a stream falls back to the task row. Persistence and
`Last-Event-ID` replay are deferred to F04/F05; `RunHub.publish` is the single
place that will need to change, and every event already carries the fields a
replayable store needs.

Everything here runs on the event loop thread and never awaits while it holds
state, so "take the text so far and start listening" is atomic: a new
subscriber can neither miss nor repeat a chunk.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

TURN_STARTED = "turn.started"
TEXT_DELTA = "text.delta"
TURN_COMPLETED = "turn.completed"
TURN_FAILED = "turn.failed"
RUNTIME_STATUS = "runtime.status"
TOOL_STARTED = "tool.started"
TOOL_COMPLETED = "tool.completed"
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_RESOLVED = "approval.resolved"

# Events a stream that connects late is given again, so the tool timeline is
# not lost on a page refresh (bounded; everything else is live-only).
REPLAYED_TYPES = {TOOL_STARTED, TOOL_COMPLETED}
MAX_REPLAYED_EVENTS = 200

TERMINAL_TYPES = {TURN_COMPLETED, TURN_FAILED}

SUBSCRIBER_QUEUE_SIZE = 256
FINISHED_RUN_LINGER_SECONDS = 60.0
HEARTBEAT_SECONDS = 15.0


@dataclass(frozen=True)
class RunEvent:
    run_id: int
    seq: int
    type: str
    data: dict
    ts: str

    def envelope(self) -> dict:
        return {
            "run_id": self.run_id,
            "seq": self.seq,
            "type": self.type,
            "data": self.data,
            "ts": self.ts,
        }

    @property
    def terminal(self) -> bool:
        return self.type in TERMINAL_TYPES


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _Lagged:
    """Put on a subscriber's queue when it fell too far behind."""


class _Ended:
    pass


@dataclass(eq=False)
class Subscription:
    run_id: int
    # Events to deliver first: the text so far (and the outcome, when the run
    # already finished), then whatever is published live.
    initial: list[RunEvent]
    queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
    )
    done: bool = False


@dataclass
class _Run:
    run_id: int
    seq: int = 0
    text_parts: list[str] = field(default_factory=list)
    replay: list[RunEvent] = field(default_factory=list)
    terminal: RunEvent | None = None
    subscribers: set[Subscription] = field(default_factory=set)
    cleanup: asyncio.TimerHandle | None = None


class RunHub:
    def __init__(self) -> None:
        self._runs: dict[int, _Run] = {}

    def start(self, run_id: int) -> None:
        """Registers a run. Safe to call again for a run already registered."""
        if run_id not in self._runs:
            self._runs[run_id] = _Run(run_id)

    def is_live(self, run_id: int) -> bool:
        run = self._runs.get(run_id)
        return run is not None and run.terminal is None

    def publish(self, run_id: int, event_type: str, data: dict | None = None) -> RunEvent | None:
        run = self._runs.get(run_id)
        if run is None or run.terminal is not None:
            return None
        run.seq += 1
        event = RunEvent(run_id, run.seq, event_type, dict(data or {}), _now())
        if event_type == TEXT_DELTA:
            run.text_parts.append(str(event.data.get("text", "")))
        if event_type in REPLAYED_TYPES and len(run.replay) < MAX_REPLAYED_EVENTS:
            run.replay.append(event)
        if event.terminal:
            run.terminal = event
        for subscriber in list(run.subscribers):
            self._deliver(run, subscriber, event)
        if event.terminal:
            run.subscribers.clear()
            self._schedule_cleanup(run)
        return event

    def subscribe(self, run_id: int) -> Subscription | None:
        """None means this process knows nothing (more) about the run; the
        caller answers from the task row instead."""
        run = self._runs.get(run_id)
        if run is None:
            return None
        initial: list[RunEvent] = list(run.replay)
        text = "".join(run.text_parts)
        if text:
            initial.append(
                RunEvent(run_id, run.seq, TEXT_DELTA, {"text": text, "mode": "replace"}, _now())
            )
        subscription = Subscription(run_id=run_id, initial=initial)
        if run.terminal is not None:
            initial.append(run.terminal)
            subscription.queue.put_nowait(_Ended())
        else:
            run.subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        run = self._runs.get(subscription.run_id)
        if run is not None:
            run.subscribers.discard(subscription)

    def subscriber_count(self, run_id: int) -> int:
        run = self._runs.get(run_id)
        return len(run.subscribers) if run else 0

    def forget(self, run_id: int) -> None:
        run = self._runs.pop(run_id, None)
        if run is not None and run.cleanup is not None:
            run.cleanup.cancel()

    def _deliver(self, run: _Run, subscriber: Subscription, event: RunEvent) -> None:
        try:
            subscriber.queue.put_nowait(event)
            if event.terminal:
                subscriber.queue.put_nowait(_Ended())
        except asyncio.QueueFull:
            # A slow reader must never hold the run back. Drop what it had
            # not read; it is told to fall back to polling the task.
            run.subscribers.discard(subscriber)
            while not subscriber.queue.empty():
                subscriber.queue.get_nowait()
            subscriber.queue.put_nowait(_Lagged())

    def _schedule_cleanup(self, run: _Run) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        run.cleanup = loop.call_later(FINISHED_RUN_LINGER_SECONDS, self.forget, run.run_id)


hub = RunHub()


def format_sse(event: RunEvent) -> str:
    return (
        f"id: {event.seq}\n"
        f"event: {event.type}\n"
        f"data: {json.dumps(event.envelope(), separators=(',', ':'))}\n\n"
    )


def format_comment(text: str) -> str:
    return f": {text}\n\n"


async def stream_frames(
    subscription: Subscription,
    *,
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    run_hub: RunHub = hub,
) -> AsyncIterator[str]:
    """SSE frames for one subscriber. Ends after the terminal event. A
    disconnect cancels this generator; the run itself carries on."""
    try:
        for event in subscription.initial:
            yield format_sse(event)
            if event.terminal:
                return
        while True:
            try:
                item = await asyncio.wait_for(subscription.queue.get(), heartbeat_seconds)
            except TimeoutError:
                yield format_comment("keepalive")
                continue
            if isinstance(item, RunEvent):
                yield format_sse(item)
                if item.terminal:
                    return
            elif isinstance(item, _Lagged):
                lagged = RunEvent(
                    subscription.run_id, 0, RUNTIME_STATUS, {"state": "lagged"}, _now()
                )
                yield format_sse(lagged)
                return
            else:
                return
    finally:
        subscription.done = True
        run_hub.unsubscribe(subscription)
