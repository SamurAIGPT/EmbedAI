"""The data lease: one backend per SQLite database, safe after a crash."""

import json
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.core import data_lease
from app.core.config import settings

BACKEND_DIR = Path(__file__).parents[2]

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX signals")

HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from app.core import data_lease
    try:
        data_lease.acquire(Path(sys.argv[1]))
    except data_lease.DataDirInUse as exc:
        print("IN_USE " + str(exc), flush=True)
        sys.exit(7)
    print("HELD", flush=True)
    if sys.argv[2:] == ["hold"]:
        time.sleep(600)
    """
)


def run_holder(database: Path, *, hold: bool) -> subprocess.Popen:
    args = [sys.executable, "-c", HOLDER, str(database)] + (["hold"] if hold else [])
    return subprocess.Popen(
        args, cwd=BACKEND_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
    )


def start_holding(database: Path) -> subprocess.Popen:
    proc = run_holder(database, hold=True)
    line = proc.stdout.readline().strip()
    assert line == "HELD", (line, proc.stderr.read() if proc.poll() is not None else "")
    return proc


def stop(proc: subprocess.Popen, sig=signal.SIGTERM) -> None:
    if proc.poll() is None:
        proc.send_signal(sig)
        proc.wait(timeout=10)


def test_second_process_is_refused_and_told_who_holds_it(tmp_path):
    database = tmp_path / "data" / "open-dots.db"
    holder = start_holding(database)
    try:
        second = run_holder(database, hold=False)
        out, _ = second.communicate(timeout=30)
        assert second.returncode == 7
        assert out.startswith("IN_USE ")
        assert f"process {holder.pid}" in out
        assert str(database.resolve()) in out
        with pytest.raises(data_lease.DataDirInUse) as refused:
            data_lease.acquire(database)
        assert refused.value.holder.get("pid") == holder.pid
    finally:
        stop(holder)


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_a_dead_holder_leaves_no_stale_lock(tmp_path, sig):
    database = tmp_path / "open-dots.db"
    holder = start_holding(database)
    stop(holder, sig)
    # The lease file is still there, naming the dead process; it is not a lock.
    assert json.loads(data_lease.lease_path(database.resolve()).read_text())["pid"] == holder.pid
    lease = data_lease.acquire(database)
    try:
        assert lease.held
        assert json.loads(lease.path.read_text())["pid"] == os.getpid()
    finally:
        lease.release()


def test_acquire_is_shared_within_one_process_and_released_last(tmp_path):
    database = tmp_path / "open-dots.db"
    first = data_lease.acquire(database)
    second = data_lease.acquire(database)
    assert first is second
    first.release()
    # Still held: another process is refused.
    other = run_holder(database, hold=False)
    other.communicate(timeout=30)
    assert other.returncode == 7
    second.release()
    assert not second.held
    other = run_holder(database, hold=False)
    out, _ = other.communicate(timeout=30)
    assert other.returncode == 0, out


def test_different_databases_in_one_folder_do_not_conflict(tmp_path):
    holder = start_holding(tmp_path / "a.db")
    try:
        lease = data_lease.acquire(tmp_path / "b.db")
        lease.release()
    finally:
        stop(holder)


def test_postgres_takes_no_file_lease(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_BACKEND", "postgres")
    assert data_lease.acquire_for_settings(settings) is None


async def test_app_startup_refuses_a_database_another_backend_holds(monkeypatch, tmp_path):
    from app.main import app

    database = tmp_path / "busy.db"
    monkeypatch.setattr(settings, "DATABASE_BACKEND", "sqlite")
    monkeypatch.setattr(settings, "SQLITE_PATH", str(database))
    holder = start_holding(database)
    try:
        with pytest.raises(data_lease.DataDirInUse):
            async with app.router.lifespan_context(app):
                pytest.fail("the app started on a database another backend holds")
    finally:
        stop(holder)
    # Nothing of this process's lease is left behind either.
    assert data_lease.lease_path(database.resolve()) not in data_lease._held
