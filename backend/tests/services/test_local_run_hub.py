import asyncio
import json

from app.services import local_run_hub
from app.services.local_run_hub import (
    RunEvent,
    RunHub,
    format_sse,
    stream_frames,
)


def append(hub, run_id, text):
    return hub.publish(run_id, "text.delta", {"text": text, "mode": "append"})


def test_events_get_a_monotonic_sequence_and_the_envelope_fields():
    hub = RunHub()
    hub.start(7)
    first = hub.publish(7, "turn.started")
    second = append(hub, 7, "a")
    assert (first.seq, second.seq) == (1, 2)
    assert set(second.envelope()) == {"run_id", "seq", "type", "data", "ts"}
    assert second.envelope()["run_id"] == 7


def test_sequences_are_per_run():
    hub = RunHub()
    hub.start(1)
    hub.start(2)
    append(hub, 1, "a")
    append(hub, 1, "b")
    assert append(hub, 2, "x").seq == 1


def test_a_late_subscriber_gets_the_text_so_far_then_only_new_text():
    hub = RunHub()
    hub.start(1)
    hub.publish(1, "turn.started")
    append(hub, 1, "Hel")
    append(hub, 1, "lo")

    sub = hub.subscribe(1)
    assert [(e.type, e.data) for e in sub.initial] == [
        ("text.delta", {"text": "Hello", "mode": "replace"}),
    ]
    append(hub, 1, " world")
    live = sub.queue.get_nowait()
    assert live.data == {"text": " world", "mode": "append"}
    assert sub.queue.empty()
    # Nothing missing and nothing repeated.
    assert sub.initial[0].data["text"] + live.data["text"] == "Hello world"


def test_a_subscriber_before_any_text_gets_no_snapshot():
    hub = RunHub()
    hub.start(1)
    assert hub.subscribe(1).initial == []


def test_an_unknown_run_has_no_subscription():
    assert RunHub().subscribe(99) is None


def test_a_finished_run_replays_its_text_and_outcome():
    hub = RunHub()
    hub.start(1)
    append(hub, 1, "done")
    hub.publish(1, "turn.completed", {"status": "succeeded", "result": "done"})
    sub = hub.subscribe(1)
    assert [e.type for e in sub.initial] == ["text.delta", "turn.completed"]
    assert sub.initial[-1].terminal


def test_nothing_is_published_after_the_outcome():
    hub = RunHub()
    hub.start(1)
    hub.publish(1, "turn.failed", {"status": "failed", "error": "x", "result": ""})
    assert append(hub, 1, "late") is None
    assert not hub.is_live(1)


def test_the_outcome_reaches_current_subscribers_and_closes_them():
    hub = RunHub()
    hub.start(1)
    sub = hub.subscribe(1)
    hub.publish(1, "turn.completed", {"status": "succeeded", "result": ""})
    assert sub.queue.get_nowait().terminal
    assert hub.subscriber_count(1) == 0


def test_a_slow_subscriber_never_blocks_publishing_and_is_told_to_poll():
    hub = RunHub()
    hub.start(1)
    slow = hub.subscribe(1)
    fast = hub.subscribe(1)
    for index in range(local_run_hub.SUBSCRIBER_QUEUE_SIZE + 50):
        assert append(hub, 1, "x") is not None
        while not fast.queue.empty():
            fast.queue.get_nowait()
    assert hub.subscriber_count(1) == 1  # the slow one was dropped, the fast one stays
    assert slow.queue.qsize() == 1
    assert isinstance(slow.queue.get_nowait(), local_run_hub._Lagged)
    # the run itself kept going
    assert hub.publish(1, "turn.completed", {"status": "succeeded", "result": "x"}) is not None


async def test_a_lagged_stream_says_so_and_ends():
    hub = RunHub()
    hub.start(1)
    slow = hub.subscribe(1)
    for _ in range(local_run_hub.SUBSCRIBER_QUEUE_SIZE + 5):
        append(hub, 1, "x")
    frames = [frame async for frame in stream_frames(slow, run_hub=hub)]
    assert len(frames) == 1
    payload = json.loads(frames[0].split("data: ")[1])
    assert payload["type"] == "runtime.status" and payload["data"] == {"state": "lagged"}


async def test_the_stream_ends_after_the_outcome_and_unsubscribes():
    hub = RunHub()
    hub.start(1)
    sub = hub.subscribe(1)
    append(hub, 1, "hi")
    hub.publish(1, "turn.completed", {"status": "succeeded", "result": "hi"})
    frames = [frame async for frame in stream_frames(sub, run_hub=hub)]
    types = [json.loads(f.split("data: ")[1])["type"] for f in frames]
    assert types == ["text.delta", "turn.completed"]
    assert hub.subscriber_count(1) == 0


async def test_closing_a_stream_early_unsubscribes_it():
    hub = RunHub()
    hub.start(1)
    sub = hub.subscribe(1)
    stream = stream_frames(sub, heartbeat_seconds=60, run_hub=hub)
    append(hub, 1, "hi")
    first = await stream.__anext__()
    assert "text.delta" in first
    await stream.aclose()  # what a dropped connection does
    assert hub.subscriber_count(1) == 0
    assert hub.is_live(1)  # the run itself is untouched


async def test_a_cancelled_stream_unsubscribes():
    hub = RunHub()
    hub.start(1)
    sub = hub.subscribe(1)

    async def consume():
        async for _ in stream_frames(sub, heartbeat_seconds=60, run_hub=hub):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.02)
    assert hub.subscriber_count(1) == 1
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert hub.subscriber_count(1) == 0


async def test_an_idle_stream_sends_keepalives():
    hub = RunHub()
    hub.start(1)
    sub = hub.subscribe(1)
    stream = stream_frames(sub, heartbeat_seconds=0.02, run_hub=hub)
    assert (await stream.__anext__()).startswith(":")
    await stream.aclose()


async def test_live_text_reaches_a_waiting_stream():
    hub = RunHub()
    hub.start(1)
    sub = hub.subscribe(1)
    stream = stream_frames(sub, heartbeat_seconds=5, run_hub=hub)
    waiting = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0.01)
    append(hub, 1, "now")
    frame = await asyncio.wait_for(waiting, 2)
    assert json.loads(frame.split("data: ")[1])["data"]["text"] == "now"
    await stream.aclose()


def test_frames_use_the_sse_layout():
    event = RunEvent(3, 9, "text.delta", {"text": "a\nb", "mode": "append"}, "2026-01-01T00:00:00+00:00")
    frame = format_sse(event)
    assert frame.startswith("id: 9\nevent: text.delta\ndata: ")
    assert frame.endswith("\n\n")
    assert "\n" not in frame.split("data: ")[1].rstrip("\n")  # newlines in text are escaped
    assert json.loads(frame.split("data: ")[1]) == event.envelope()


async def test_a_finished_run_is_forgotten_after_the_linger(monkeypatch):
    monkeypatch.setattr(local_run_hub, "FINISHED_RUN_LINGER_SECONDS", 0.05)
    hub = RunHub()
    hub.start(1)
    hub.publish(1, "turn.completed", {"status": "succeeded", "result": ""})
    assert hub.subscribe(1) is not None
    await asyncio.sleep(0.15)
    assert hub.subscribe(1) is None
