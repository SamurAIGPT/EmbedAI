import sqlite3

from tests.db.sqlite_helpers import run_alembic

PREVIOUS_HEAD = "a4c91d7e2f60"
NEW_REVISION = "c8d2e5a9f314"


def test_existing_routines_and_runs_get_retry_off_and_attempt_one(tmp_path):
    database = tmp_path / "routines.db"
    baseline = run_alembic(database, "upgrade", PREVIOUS_HEAD)
    assert baseline.returncode == 0, baseline.stderr

    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO routine (owner_id, agent_id, name, prompt, schedule_kind, schedule_spec) "
        "VALUES ('owner', 1, 'Report', 'Say hello', 'manual', '{}')"
    )
    connection.execute("INSERT INTO routine_run (routine_id, trigger, state) VALUES (1, 'manual', 'failed')")
    connection.commit()
    connection.close()

    upgraded = run_alembic(database, "upgrade", NEW_REVISION)
    assert upgraded.returncode == 0, upgraded.stderr
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT retry_limit, retry_delay_minutes FROM routine").fetchone() == (0, 5)
        assert connection.execute("SELECT attempt, retry_of FROM routine_run").fetchone() == (1, None)
        connection.execute(
            "INSERT INTO routine_run (routine_id, trigger, state, retry_of) VALUES (1, 'retry', 'queued', 1)"
        )
        try:
            connection.execute(
                "INSERT INTO routine_run (routine_id, trigger, state, retry_of) VALUES (1, 'retry', 'queued', 1)"
            )
            raise AssertionError("a second retry of the same run was accepted")
        except sqlite3.IntegrityError:
            pass
    finally:
        connection.close()

    downgraded = run_alembic(database, "downgrade", PREVIOUS_HEAD)
    assert downgraded.returncode == 0, downgraded.stderr
