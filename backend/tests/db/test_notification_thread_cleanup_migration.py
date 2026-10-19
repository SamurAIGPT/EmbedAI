import sqlite3

from tests.db.sqlite_helpers import BACKEND_DIR, run_alembic


PREVIOUS_HEAD = "f3b8d1e6a274"
NEW_REVISION = "a4c91d7e2f60"


def test_upgrade_detaches_retained_notifications_from_deleted_threads(tmp_path):
    database = tmp_path / "notifications.db"
    baseline = run_alembic(database, "upgrade", PREVIOUS_HEAD)
    assert baseline.returncode == 0, baseline.stderr

    connection = sqlite3.connect(database)
    connection.execute(
        """
        INSERT INTO notification (owner_id, kind, title, body, thread_id, read_at)
        VALUES ('owner', 'routine_completed', 'Finished', 'Result', 987654, '2026-10-19 00:00:00')
        """
    )
    notification_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.commit()
    connection.close()

    upgraded = run_alembic(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == NEW_REVISION
        row = connection.execute(
            "SELECT kind, title, body, thread_id, read_at FROM notification WHERE id = ?",
            (notification_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row == ("routine_completed", "Finished", "Result", None, "2026-10-19 00:00:00")
