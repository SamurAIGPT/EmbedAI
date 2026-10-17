"""Engine processes left by a hard kill are found and ended at the next start,
and nothing else is (app/core/engine_processes.py). Real processes throughout:
a "backend" that died, its engine group (leader and child), a stand-in for a
computer's `docker exec` in a session of its own, and unrelated programs that
must survive, including one that holds a recorded pid's number."""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from app.core import engine_processes as ep

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

SLEEPER = "import sys, time; time.sleep(120)"
# A leader that starts a child in its own group, like an engine and its MCP server.
WITH_CHILD = (
    "import subprocess, sys, time; "
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)', 'engine-child']); "
    "print(c.pid, flush=True); time.sleep(120)"
)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def wait_gone(pid: int, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def procs():
    started: list[subprocess.Popen] = []

    def spawn(code: str, *extra: str, env: dict | None = None, session: bool = True, stdout=None) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-c", code, *extra], env=env, start_new_session=session,
                             stdout=stdout, stderr=subprocess.DEVNULL)
        started.append(p)
        return p

    yield spawn
    for p in started:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            p.kill()
        except OSError:
            pass
        p.wait(timeout=5)


def dead_owner(procs) -> str:
    """The marker token of a backend that has exited."""
    p = procs(SLEEPER, "a-backend")
    token = f"{p.pid}-{ep.start_token(p.pid)}"
    p.kill()
    p.wait(timeout=5)
    return token


def write_record(pid: int, owner: str, *, program: str | None = None, started: str | None = None) -> None:
    folder = ep.records_dir()
    folder.mkdir(parents=True, exist_ok=True)
    info = ep.process_info(pid)
    (folder / f"{pid}.json").write_text(json.dumps({
        "pid": pid,
        "started": started if started is not None else info.started,
        "program": program if program is not None else os.path.basename(sys.executable),
        "owner": owner,
    }))


def marked(owner: str) -> dict:
    return {**os.environ, ep.MARKER_ENV: owner}


def test_record_and_forget(procs):
    p = procs(SLEEPER)
    ep.record(p.pid, sys.executable)
    entry = json.loads((ep.records_dir() / f"{p.pid}.json").read_text())
    assert entry["pid"] == p.pid and entry["owner"] == ep.owner_token()
    assert entry["started"] == ep.process_info(p.pid).started
    ep.forget(p.pid)
    assert not (ep.records_dir() / f"{p.pid}.json").exists()


def test_mark_names_this_backend():
    env = ep.mark({})
    assert env[ep.MARKER_ENV] == ep.owner_token()
    assert ep.owner_token().startswith(f"{os.getpid()}-")
    assert ep.owner_alive(ep.owner_token())


def test_a_crashed_backends_engine_group_and_its_stray_exec_are_ended(procs):
    owner = dead_owner(procs)
    leader = procs(WITH_CHILD, "fake-engine", env=marked(owner), stdout=subprocess.PIPE)
    child = int(leader.stdout.readline())
    # A computer's `docker exec`: inherits the marker, runs in a session of its own.
    stray = procs(SLEEPER, "docker-exec-stand-in", env=marked(owner))
    bystander = procs(SLEEPER, "fake-engine")  # same program, not ours
    write_record(leader.pid, owner)
    time.sleep(0.2)

    report = ep.sweep()

    assert leader.pid in report.killed_groups
    assert stray.pid in report.killed_pids
    assert wait_gone(leader.pid) and wait_gone(child) and wait_gone(stray.pid)
    assert alive(bystander.pid)
    assert not list(ep.records_dir().glob("*.json"))


def test_a_recycled_pid_is_never_signalled(procs):
    owner = dead_owner(procs)
    # The recorded pid now runs another program that started later: its start
    # time differs from the record's.
    other = procs(SLEEPER, "someone-else")
    write_record(other.pid, owner, started="Thu Jan  1 00:00:00 1970")
    # The right start time but another program, without the marker.
    other_two = procs(SLEEPER, "someone-else-too")
    write_record(other_two.pid, owner, program="claude")

    report = ep.sweep()

    assert not report.killed_groups and not report.killed_pids
    assert alive(other.pid) and alive(other_two.pid)
    assert not list(ep.records_dir().glob("*.json"))


def test_a_live_backends_engines_are_left_alone(procs):
    backend = procs(SLEEPER, "another-backend")  # still running
    owner = f"{backend.pid}-{ep.start_token(backend.pid)}"
    engine = procs(SLEEPER, "its-engine", env=marked(owner))
    write_record(engine.pid, owner)

    report = ep.sweep()

    assert engine.pid in report.left_alone
    assert alive(engine.pid)
    assert (ep.records_dir() / f"{engine.pid}.json").exists()


def test_leader_gone_only_marked_members_are_ended(procs):
    owner = dead_owner(procs)
    leader = procs(WITH_CHILD, "fake-engine", env=marked(owner), stdout=subprocess.PIPE)
    child = int(leader.stdout.readline())
    write_record(leader.pid, owner)
    os.kill(leader.pid, signal.SIGKILL)
    leader.wait(timeout=5)
    assert alive(child)

    report = ep.sweep()

    assert child in report.killed_pids
    assert wait_gone(child)


def test_what_a_cleanly_stopped_backend_left_in_the_background_stays(procs):
    owner = dead_owner(procs)  # exited, and left no records behind
    background = procs(SLEEPER, "dev-server", env=marked(owner))

    report = ep.sweep()

    assert background.pid not in report.killed_pids
    assert alive(background.pid)
