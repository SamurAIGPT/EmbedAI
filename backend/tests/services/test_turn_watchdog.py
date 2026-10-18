"""The turn watchdog: an engine that does nothing for TURN_STALL_SECONDS is
stopped and its turn fails with `engine_stalled`; a turn waiting on a person
is never stalled; an engine whose process dies ends its turn at once."""

import asyncio
import os
import signal
import sys

import pytest

from app.core.config import settings
from app.db.models import Turn
from app.services import thread_runtime
from app.services.provider_adapter import ProviderStatus, TurnOutcome
from tests.services.thread_harness import *  # noqa: F401,F403
from tests.services.thread_harness import (
    finished,
    new_thread,
    pending_approval,
    reload,
    types_of,
    wait_until,
)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        # A zombie (dead, not yet reaped) is gone for this purpose.
        waited, _ = os.waitpid(pid, os.WNOHANG)
        return waited == 0
    except ChildProcessError:
        return True  # not our child; it answered signal 0, so it exists


@pytest.fixture
def stall(monkeypatch):
    monkeypatch.setattr(settings, "TURN_STALL_SECONDS", 0.6)
    monkeypatch.setattr(thread_runtime, "STALL_GRACE_SECONDS", 0.3)


class StubbornAdapter:
    """An engine whose adapter ignores Stop and never returns: the worst case."""

    key = "stubborn"
    label = "Stubborn"

    def new_session_id(self):
        return None

    async def status(self):
        return ProviderStatus(ready=True, version="1")

    async def run_turn(self, ctx, sink, gate):
        await sink.emit("assistant.message", {"text": "starting"})
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise


class SlowButFineAdapter:
    """Silent for a while, then answers: fine when the watchdog is off."""

    key = "slow"
    label = "Slow"

    def new_session_id(self):
        return None

    async def status(self):
        return ProviderStatus(ready=True, version="1")

    async def run_turn(self, ctx, sink, gate):
        await asyncio.sleep(1.2)
        await sink.emit("assistant.message", {"text": "done"})
        return TurnOutcome("succeeded", result="done")


@pytest.mark.parametrize("step", ["silent", "hang"])
async def test_an_engine_that_goes_quiet_is_stopped_and_the_turn_fails_clearly(
    rt, sessions, cli, owner, stall, step, monkeypatch
):
    # Long enough for the fake CLI to start and say it is hanging on a busy
    # machine (its start-up counts as silence too), short enough to be quick.
    monkeypatch.setattr(settings, "TURN_STALL_SECONDS", 2.0)
    thread = await new_thread(sessions, owner)
    cli.script({"type": step})
    turn = await rt.send_turn(thread.id, "do it")
    hung = await wait_until(lambda: cli.log("hanging"))
    await finished(sessions, thread.id)
    done = await reload(sessions, Turn, turn.id)
    assert done.status == "failed"
    assert done.error_code == "engine_stalled"
    assert "stopped responding" in done.error
    assert (await types_of(sessions, thread.id))[-1] == "turn.failed"
    # The engine process was killed, not left running.
    pid = hung[0]["pid"]
    await wait_until(lambda: not alive(pid), timeout=10)
    assert rt._runs == {}


async def test_an_adapter_that_ignores_stop_is_given_up_on(rt, sessions, owner, stall):
    rt.adapters["stubborn"] = StubbornAdapter()
    thread = await new_thread(sessions, owner, provider="stubborn", provider_session_id=None)
    turn = await rt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    done = await reload(sessions, Turn, turn.id)
    assert (done.status, done.error_code) == ("failed", "engine_stalled")
    assert rt._runs == {}


async def test_a_turn_waiting_on_an_approval_is_not_stalled(rt, sessions, cli, owner, stall, tools_on):
    thread = await new_thread(sessions, owner)
    cli.script({"type": "tool"})
    turn = await rt.send_turn(thread.id, "write the file")
    approval = await pending_approval(sessions, thread.id)
    # Far longer than the stall time: the person is thinking, not the engine.
    await asyncio.sleep(2.0)
    assert (await reload(sessions, Turn, turn.id)).status == "running"
    assert await rt.answer_approval(approval.id, "allow")
    await finished(sessions, thread.id)
    assert (await reload(sessions, Turn, turn.id)).status == "succeeded"


async def test_zero_turns_the_watchdog_off(rt, sessions, owner, monkeypatch):
    monkeypatch.setattr(settings, "TURN_STALL_SECONDS", 0)
    rt.adapters["slow"] = SlowButFineAdapter()
    thread = await new_thread(sessions, owner, provider="slow", provider_session_id=None)
    turn = await rt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    assert (await reload(sessions, Turn, turn.id)).status == "succeeded"


async def test_a_long_quiet_wait_shorter_than_the_stall_time_is_fine(rt, sessions, owner, monkeypatch):
    monkeypatch.setattr(settings, "TURN_STALL_SECONDS", 5)
    rt.adapters["slow"] = SlowButFineAdapter()
    thread = await new_thread(sessions, owner, provider="slow", provider_session_id=None)
    turn = await rt.send_turn(thread.id, "go")
    await finished(sessions, thread.id)
    assert (await reload(sessions, Turn, turn.id)).status == "succeeded"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
async def test_an_engine_process_that_dies_ends_its_turn_at_once(rt, sessions, cli, owner, monkeypatch):
    # The watchdog is far away: the death itself must end the turn.
    monkeypatch.setattr(settings, "TURN_STALL_SECONDS", 600)
    thread = await new_thread(sessions, owner)
    cli.script({"type": "silent"})
    turn = await rt.send_turn(thread.id, "go")
    hung = await wait_until(lambda: cli.log("hanging"))
    os.kill(hung[0]["pid"], signal.SIGKILL)
    await finished(sessions, thread.id)
    done = await reload(sessions, Turn, turn.id)
    assert done.status == "failed"
    assert done.error_code and done.error_code != "engine_stalled"
    assert done.error
