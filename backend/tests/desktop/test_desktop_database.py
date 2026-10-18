"""The pieces of the desktop database step that do not need a running backend:
the pre-migration copy and its pruning (D-012 decision 7). The whole step, with a
real backend process, is in test_desktop_entry.py."""

import sqlite3
from pathlib import Path

import pytest

from app.desktop import database


def make_database(path: Path, rows: int = 3) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")  # the app's own mode
    connection.execute("CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT)")
    connection.executemany("INSERT INTO note (body) VALUES (?)", [(f"row {n}",) for n in range(rows)])
    connection.commit()
    connection.close()


def test_a_backup_holds_what_the_database_held_and_the_original_is_untouched(tmp_path):
    source = tmp_path / "open-dots.db"
    make_database(source)
    before = sqlite3.connect(source).execute("SELECT count(*) FROM note").fetchone()[0]

    copy = database.backup_before_migration(source, tmp_path / "backups", "abc123")

    assert copy.parent == tmp_path / "backups"
    assert copy.name.startswith("open-dots-abc123-") and copy.suffix == ".db"
    assert sqlite3.connect(copy).execute("SELECT count(*) FROM note").fetchone()[0] == before == 3
    assert sqlite3.connect(source).execute("SELECT count(*) FROM note").fetchone()[0] == 3
    assert not list((tmp_path / "backups").glob("*.part"))


def test_a_backup_is_a_snapshot_not_a_link(tmp_path):
    source = tmp_path / "open-dots.db"
    make_database(source)
    copy = database.backup_before_migration(source, tmp_path / "backups", "abc123")
    connection = sqlite3.connect(source)
    connection.execute("DELETE FROM note")
    connection.commit()
    connection.close()
    assert sqlite3.connect(copy).execute("SELECT count(*) FROM note").fetchone()[0] == 3


def test_a_failed_backup_leaves_no_partial_file_and_raises(tmp_path, monkeypatch):
    source = tmp_path / "open-dots.db"
    make_database(source)

    real_connect = sqlite3.connect

    class Failing(sqlite3.Connection):
        def backup(self, target, **kwargs):
            raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(database.sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Failing, **k))
    with pytest.raises(sqlite3.OperationalError):
        database.backup_before_migration(source, tmp_path / "backups", "abc123")
    assert list((tmp_path / "backups").iterdir()) == []


def test_only_the_newest_backups_are_kept_and_nothing_else_is_touched(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    names = [f"open-dots-rev-2026010{n}T000000000000Z.db" for n in range(1, 9)]
    for name in names:
        (backups / name).write_bytes(b"x")
    (backups / "notes.txt").write_text("mine", encoding="utf-8")
    (backups / "other.db").write_bytes(b"x")
    (backups / "open-dots-rev-20260101T000000000000Z.db.part").write_bytes(b"x")
    live = tmp_path / "open-dots.db"
    live.write_bytes(b"live")

    database.prune_backups(backups)

    kept = sorted(path.name for path in backups.iterdir())
    assert kept == sorted(["notes.txt", "other.db", *names[-database.BACKUPS_KEPT:]])
    assert live.read_bytes() == b"live"


def test_how_many_backups_are_kept_is_five():
    assert database.BACKUPS_KEPT == 5
