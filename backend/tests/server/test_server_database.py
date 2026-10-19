"""`python -m app.server migrate`: create or update the schema without losing data
(app/server/database.py). SQLite only; PostgreSQL migrations are covered by CI."""

import sqlite3
from pathlib import Path

import pytest

from app.core.config import settings
from app.server import database
from tests.conftest import using_sqlite
from tests.db.sqlite_helpers import run_alembic

pytestmark = pytest.mark.skipif(not using_sqlite(), reason="these checks use their own SQLite files")


@pytest.fixture
def target(tmp_path, monkeypatch) -> Path:
    file = tmp_path / "data" / "open-dots.db"
    monkeypatch.setattr(settings, "SQLITE_PATH", str(file))
    return file


def revision(file: Path) -> str | None:
    connection = sqlite3.connect(file)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        connection.close()


def test_a_new_database_is_created_at_the_newest_revision_with_no_backup(target):
    report = database.prepare_database()
    assert target.is_file() and revision(target) == database.newest_revision()
    assert report.before is None and report.after == database.newest_revision()
    assert report.backup is None and report.changed
    assert not (target.parent / "backups").exists()


def test_running_it_again_changes_nothing_and_copies_nothing(target):
    database.prepare_database()
    report = database.prepare_database()
    assert report.before == report.after and not report.changed and report.backup is None
    assert not (target.parent / "backups").exists()


def test_a_database_behind_is_copied_aside_first_then_updated(target):
    database.prepare_database()
    assert run_alembic(target, "downgrade", "-1").returncode == 0
    behind = revision(target)
    assert behind != database.newest_revision()
    connection = sqlite3.connect(target)
    connection.execute("CREATE TABLE IF NOT EXISTS keepsake (n INTEGER)")
    connection.execute("INSERT INTO keepsake VALUES (42)")
    connection.commit()
    connection.close()

    report = database.prepare_database()

    assert report.before == behind and report.after == database.newest_revision() and report.changed
    assert revision(target) == database.newest_revision()
    assert report.backup is not None and report.backup.parent == target.parent / "backups"
    assert behind in report.backup.name
    assert revision(report.backup) == behind  # the copy is the state before the update
    assert sqlite3.connect(report.backup).execute("SELECT n FROM keepsake").fetchone() == (42,)
    assert sqlite3.connect(target).execute("SELECT n FROM keepsake").fetchone() == (42,)  # data survives


def test_a_file_this_app_did_not_make_is_refused_and_left_exactly_as_it_was(target):
    target.parent.mkdir(parents=True)
    connection = sqlite3.connect(target)
    connection.execute("CREATE TABLE somebody_elses (id INTEGER)")
    connection.commit()
    connection.close()
    before = target.read_bytes()

    with pytest.raises(database.DatabaseRefused, match="schema version is unknown"):
        database.prepare_database()
    assert target.read_bytes() == before


def test_a_file_that_is_not_a_database_is_refused_and_left_alone(target):
    target.parent.mkdir(parents=True)
    target.write_bytes(b"this is not sqlite, it is a text file " * 20)
    before = target.read_bytes()
    with pytest.raises(database.DatabaseRefused):
        database.prepare_database()
    assert target.read_bytes() == before


def test_a_wrong_encryption_key_is_reported_before_anything_changes(target):
    database.prepare_database()
    assert run_alembic(target, "downgrade", "-1").returncode == 0
    connection = sqlite3.connect(target)
    connection.execute(
        "INSERT INTO user_credential (user_id, provider, file_path, ciphertext) VALUES ('u1', 'claude', 'f', 'not-a-valid-token')"
    )
    connection.commit()
    connection.close()
    before = target.read_bytes()

    with pytest.raises(database.KeyMismatch):
        database.prepare_database()
    assert target.read_bytes() == before
    assert not (target.parent / "backups").exists()
