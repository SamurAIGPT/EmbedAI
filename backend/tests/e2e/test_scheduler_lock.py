"""Two real backend processes on one PostgreSQL database: only one runs the
routine scheduler, a Run now sent to the other is still started exactly once
(by the holder), and after a `kill -9` of the holder the other takes over and
runs the next one (app/core/scheduler_lock.py, docs/RELIABILITY.md).

PostgreSQL only: it needs a server it may create a scratch database on (the
CI service and a local `postgresql@14` both allow that). The database is
dropped afterwards. On SQLite the file lease refuses a second backend, which
tests/core/test_data_lease.py covers."""

import signal
import sys
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from tests.conftest import using_sqlite
from tests.e2e.backend import Backend, wait_for

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals"),
    pytest.mark.skipif(using_sqlite(), reason="the scheduler lock is PostgreSQL's"),
]

RUNS = "this backend runs the routine scheduler"
STANDS_BY = "stands by"


@pytest.fixture
def scratch_database():
    name = f"od_sched_{uuid.uuid4().hex[:10]}"
    admin = create_engine(settings.POSTGRES_SYNC_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(settings.POSTGRES_SYNC_URL).set(database=name)
    try:
        yield name, url
    finally:
        with admin.connect() as conn:
            conn.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name AND pid <> pg_backend_pid()"
            ), {"name": name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def postgres_env(name: str) -> dict:
    return {
        "DATABASE_BACKEND": "postgres",
        "POSTGRES_HOST": settings.POSTGRES_HOST,
        "POSTGRES_PORT": str(settings.POSTGRES_PORT),
        "POSTGRES_USER": settings.POSTGRES_USER,
        "POSTGRES_PASSWORD": settings.POSTGRES_PASSWORD,
        "POSTGRES_DB": name,
        "LOOPBACK_TRUST": "programs",
    }


def test_one_scheduler_per_database_and_a_takeover_after_kill_9(tmp_path, scratch_database):
    name, url = scratch_database
    db = create_engine(url)

    def run_state(run_id: int) -> tuple:
        with db.connect() as conn:
            return conn.execute(text("SELECT state, turn_id FROM routine_run WHERE id = :id"), {"id": run_id}).one()

    def turns_for(run_id: int) -> int:
        with db.connect() as conn:
            return conn.execute(text(
                "SELECT count(*) FROM turn t JOIN routine_run r ON r.thread_id = t.thread_id WHERE r.id = :id"
            ), {"id": run_id}).scalar_one()

    first = Backend(tmp_path / "a", env=postgres_env(name))
    second = Backend(tmp_path / "b", env=postgres_env(name))
    try:
        first.start()
        wait_for(lambda: RUNS in first.log(), what="the first backend to take the scheduler")
        second.start(migrate=False)
        wait_for(lambda: STANDS_BY in second.log(), what="the second backend to stand by")
        assert RUNS not in second.log()

        with second.client() as http:
            agent = http.post("/api/v1/agents", json={"name": "Night shift"})
            assert agent.status_code == 201, agent.text
            routine = http.post("/api/v1/routines", json={
                "agent_id": agent.json()["id"], "name": "Nightly", "prompt": "check things",
            })
            assert routine.status_code == 201, routine.text
            routine_id = routine.json()["id"]
            # Sent to the standby: it only queues; the holder starts it.
            run = http.post(f"/api/v1/routines/{routine_id}/run")
            assert run.status_code == 202, run.text
            run_id = run.json()["id"]
        wait_for(lambda: run_state(run_id)[0] == "completed", 30, what="the holder to run it")
        assert turns_for(run_id) == 1
        # The holder's engine ran it; the standby started nothing.
        assert first.cli_log("start") and not second.cli_log("start")

        first.signal(signal.SIGKILL)
        first.wait_exit(timeout=10)
        # The server ends the dead backend's session, which frees the lock;
        # the standby takes it on its next pass.
        wait_for(lambda: RUNS in second.log(), 30, what="the standby to take over")
        with second.client() as http:
            run = http.post(f"/api/v1/routines/{routine_id}/run")
            assert run.status_code == 202, run.text
            next_id = run.json()["id"]
        wait_for(lambda: run_state(next_id)[0] == "completed", 30, what="the new holder to run it")
        assert turns_for(next_id) == 1
    finally:
        first.stop()
        second.stop()
        db.dispose()


def test_a_clean_stop_hands_the_scheduler_over_at_once(tmp_path, scratch_database):
    name, _ = scratch_database
    first = Backend(tmp_path / "a", env=postgres_env(name))
    second = Backend(tmp_path / "b", env=postgres_env(name))
    try:
        first.start()
        wait_for(lambda: RUNS in first.log(), what="the first backend to take the scheduler")
        second.start(migrate=False)
        wait_for(lambda: STANDS_BY in second.log(), what="the second backend to stand by")
        first.signal(signal.SIGTERM)
        assert first.wait_exit(timeout=30) in (0, -signal.SIGTERM), first.log()
        wait_for(lambda: RUNS in second.log(), 15, what="the standby to take over")
        with httpx.Client(base_url=second.url, timeout=10) as http:
            assert http.get("/api/v1/health").status_code == 200
    finally:
        first.stop()
        second.stop()
