"""Graceful shutdown with real signals, on the real backend process: turns and
routine runs in flight end interrupted, engine processes and their children
are gone, nothing is orphaned, and a restart picks up cleanly."""

import signal
import sys
import threading

import httpx
import pytest

from tests.e2e.backend import Backend, alive, wait_for

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and process groups")


@pytest.fixture
def backend(tmp_path):
    started: list[Backend] = []

    def make(**kwargs) -> Backend:
        b = Backend(tmp_path / f"b{len(started)}", env={"LOOPBACK_TRUST": "programs"}, **kwargs)
        started.append(b)
        return b

    yield make
    for b in started:
        b.stop()


def start_turn(b: Backend, http: httpx.Client, prompt: str = "work on it", agent_id: int | None = None) -> tuple[int, int]:
    body = {"agent_id": agent_id} if agent_id is not None else {}
    thread = http.post("/api/v1/threads", json=body)
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["id"]
    turn = http.post(f"/api/v1/threads/{thread_id}/turns", json={"prompt_text": prompt})
    assert turn.status_code == 202, turn.text
    return thread_id, turn.json()["id"]


def hanging(b: Backend, count: int = 1) -> list[dict]:
    return wait_for(lambda: len(b.cli_log("hanging")) >= count and b.cli_log("hanging"), what="the engine to start")


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_mid_turn_interrupts_it_and_leaves_no_process_behind(backend, sig):
    b = backend().start()
    b.script({"type": "hang_with_child"})
    with b.client() as http:
        thread_id, turn_id = start_turn(b, http)
        engine = hanging(b)[0]
        assert alive(engine["pid"]) and alive(engine["child_pid"])

        # The web page keeps an event stream open; it must not hold the stop up.
        streamed = threading.Event()

        def stream():
            try:
                with httpx.Client(base_url=b.url, timeout=60) as s:
                    with s.stream("GET", f"/api/v1/threads/{thread_id}/events/stream") as response:
                        streamed.set()
                        for _ in response.iter_bytes():
                            pass
            except httpx.HTTPError:
                pass

        reader = threading.Thread(target=stream, daemon=True)
        reader.start()
        assert streamed.wait(10)

    b.signal(sig)
    assert b.wait_exit(timeout=30) in (0, -sig), b.log()
    assert "Application shutdown complete" in b.log()

    # The engine and the child it started are gone, not orphaned.
    wait_for(lambda: not alive(engine["pid"]) and not alive(engine["child_pid"]), 10, what="the engine to end")
    status, error_code = b.query("SELECT status, error_code FROM turn WHERE id = ?", turn_id)[0]
    assert status == "interrupted"
    events = [row[0] for row in b.query("SELECT type FROM event WHERE turn_id = ? ORDER BY seq", turn_id)]
    assert events[-1] == "turn.interrupted"

    # A restart on the same data: the turn stays interrupted (never replayed),
    # and the conversation takes a new turn.
    b.script({"type": "chat", "text": "back again"})
    b.start(migrate=False)
    with b.client() as http:
        turn = http.post(f"/api/v1/threads/{thread_id}/turns", json={"prompt_text": "again"})
        assert turn.status_code == 202, turn.text
        wait_for(lambda: b.query("SELECT status FROM turn WHERE id = ?", turn.json()["id"])[0][0] == "succeeded",
                 what="the new turn")
    assert b.query("SELECT status FROM turn WHERE id = ?", turn_id)[0][0] == "interrupted"


def test_a_routine_run_in_flight_ends_interrupted_on_sigterm_with_a_notification(backend):
    b = backend().start()
    b.script({"type": "hang_with_child"})
    with b.client() as http:
        agent = http.post("/api/v1/agents", json={"name": "Night shift"})
        assert agent.status_code == 201, agent.text
        routine = http.post("/api/v1/routines", json={
            "agent_id": agent.json()["id"], "name": "Nightly", "prompt": "check things",
        })
        assert routine.status_code == 201, routine.text
        routine_id = routine.json()["id"]
        run = http.post(f"/api/v1/routines/{routine_id}/run")
        assert run.status_code == 202, run.text
        run_id = run.json()["id"]
        engine = hanging(b)[0]
        wait_for(lambda: b.query("SELECT state FROM routine_run WHERE id = ?", run_id)[0][0] == "running",
                 what="the run to start")

    b.signal(signal.SIGTERM)
    assert b.wait_exit(timeout=30) in (0, -signal.SIGTERM), b.log()
    wait_for(lambda: not alive(engine["pid"]) and not alive(engine["child_pid"]), 10, what="the engine to end")
    state, error = b.query("SELECT state, error FROM routine_run WHERE id = ?", run_id)[0]
    assert state == "interrupted", (state, error)
    kinds = [row[0] for row in b.query("SELECT kind FROM notification WHERE run_id = ?", run_id)]
    assert "routine_interrupted" in kinds
    turn_id = b.query("SELECT turn_id FROM routine_run WHERE id = ?", run_id)[0][0]
    assert b.query("SELECT status FROM turn WHERE id = ?", turn_id)[0][0] == "interrupted"


def test_sigkill_leaves_the_turn_for_the_next_start_to_interrupt(backend):
    """A hard kill cannot clean up: the next start finds the turn's lease
    expired and marks it interrupted, never resumed or replayed."""
    b = backend().start()
    b.script({"type": "silent"})
    with b.client() as http:
        _, turn_id = start_turn(b, http)
        engine = hanging(b)[0]
    b.signal(signal.SIGKILL)
    b.wait_exit(timeout=10)
    # The engine runs in its own session, so a hard kill of the backend leaves
    # it running until it ends by itself: kill it as the operating system's
    # service manager would (documented in docs/RELIABILITY.md).
    if alive(engine["pid"]):
        import os
        os.kill(engine["pid"], signal.SIGKILL)
    b.start(migrate=False)
    wait_for(lambda: b.query("SELECT status FROM turn WHERE id = ?", turn_id)[0][0] == "interrupted",
             timeout=40, what="the stale turn to be interrupted")
    error_code = b.query("SELECT error_code FROM turn WHERE id = ?", turn_id)[0][0]
    assert error_code
