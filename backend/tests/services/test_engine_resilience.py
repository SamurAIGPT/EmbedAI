"""A broken engine never blocks a turn on another engine, and its own turns
are refused in bounded time with the reason, never hung or turned into a 500."""

import asyncio

import pytest

from app.db.models import Turn
from app.services import thread_runtime
from app.services.thread_runtime import ProviderUnavailable
from tests.services.thread_harness import *  # noqa: F401,F403
from tests.services.thread_harness import finished, new_thread, reload


class HangingStatus:
    key = "hanging"
    label = "Hanging"

    def new_session_id(self):
        return None

    async def status(self):
        await asyncio.sleep(3600)

    async def run_turn(self, ctx, sink, gate):  # pragma: no cover - never reached
        raise AssertionError("a turn started on an engine that never said it was ready")


class RaisingStatus(HangingStatus):
    key = "raising"

    async def status(self):
        raise RuntimeError("secret detail that must not reach the person")


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(thread_runtime, "STATUS_TIMEOUT_SECONDS", 0.3)


async def test_an_engine_whose_status_hangs_is_refused_in_bounded_time(rt, sessions, owner, quick):
    rt.adapters["hanging"] = HangingStatus()
    thread = await new_thread(sessions, owner, provider="hanging", provider_session_id=None)
    with pytest.raises(ProviderUnavailable) as refused:
        await asyncio.wait_for(rt.send_turn(thread.id, "hi"), 5)
    assert "did not report its status" in str(refused.value)
    assert rt._runs == {}


async def test_an_engine_whose_status_raises_is_refused_with_the_kind_of_error_only(rt, sessions, owner, quick):
    rt.adapters["raising"] = RaisingStatus()
    thread = await new_thread(sessions, owner, provider="raising", provider_session_id=None)
    with pytest.raises(ProviderUnavailable) as refused:
        await rt.send_turn(thread.id, "hi")
    assert "RuntimeError" in str(refused.value)
    assert "secret detail" not in str(refused.value)


async def test_a_hanging_engine_does_not_hold_up_a_turn_on_another(rt, sessions, cli, owner, monkeypatch):
    monkeypatch.setattr(thread_runtime, "STATUS_TIMEOUT_SECONDS", 5)
    rt.adapters["hanging"] = HangingStatus()
    stuck = await new_thread(sessions, owner, provider="hanging", provider_session_id=None)
    fine = await new_thread(sessions, owner)
    cli.script({"type": "chat", "text": "still here"})
    pending = asyncio.create_task(rt.send_turn(stuck.id, "hi"))
    turn = await rt.send_turn(fine.id, "hello")
    await finished(sessions, fine.id)
    done = await reload(sessions, Turn, turn.id)
    assert (done.status, done.result) == ("succeeded", "still here")
    assert not pending.done()  # still waiting on its own engine, alone
    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)


async def test_a_registry_listing_survives_a_hanging_and_a_raising_engine(rt, monkeypatch):
    rt.adapters["hanging"] = HangingStatus()
    rt.adapters["raising"] = RaisingStatus()
    rt.engines.probe_timeout = 0.3
    listed = {e["id"]: e for e in await asyncio.wait_for(rt.engines.describe(refresh=True), 5)}
    assert listed["claude"]["available"] is True
    assert listed["hanging"]["available"] is False and "did not report" in listed["hanging"]["reason"]
    assert listed["raising"]["available"] is False and "secret" not in listed["raising"]["reason"]
