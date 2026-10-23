"""`python -m app.server migrate`, and the server's refusal to start on a database
that has not been migrated (app/server/entry.py, app/server/database.py)."""

import sqlite3
from pathlib import Path

import pytest

from app.core.config import settings
from app.server import database
from app.server.entry import EXIT_DATABASE, EXIT_KEY_MISMATCH, main
from tests.conftest import using_sqlite
from tests.db.sqlite_helpers import run_alembic, one_revision_back

pytestmark = pytest.mark.skipif(not using_sqlite(), reason="these checks use their own SQLite files")


@pytest.fixture
def target(tmp_path, monkeypatch) -> Path:
    file = tmp_path / "data" / "open-dots.db"
    monkeypatch.setattr(settings, "SQLITE_PATH", str(file))
    return file


@pytest.fixture
def ui(tmp_path):
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "index.html").write_text("<html></html>")
    return folder


def test_schema_state_reads_without_creating_the_file(target):
    assert database.schema_state() == "new"
    assert not target.exists() and not target.parent.exists()


def test_schema_state_tells_current_from_behind(target):
    assert main(["migrate"]) == 0
    assert database.schema_state() == "current"
    assert run_alembic(target, "downgrade", one_revision_back()).returncode == 0
    assert database.schema_state() == "behind"


def test_migrate_creates_then_reports_up_to_date(target, capsys):
    assert main(["migrate"]) == 0
    assert "Database ready at revision" in capsys.readouterr().out
    assert main(["migrate"]) == 0
    assert "already up to date" in capsys.readouterr().out


def test_migrate_says_where_the_copy_went(target, capsys):
    main(["migrate"])
    run_alembic(target, "downgrade", one_revision_back())
    capsys.readouterr()
    assert main(["migrate"]) == 0
    out = capsys.readouterr().out
    assert "Copied the database to" in out and str(target.parent / "backups") in out


def test_migrate_refuses_a_foreign_file_with_a_clear_exit_code(target, capsys):
    target.parent.mkdir(parents=True)
    connection = sqlite3.connect(target)
    connection.execute("CREATE TABLE somebody_elses (id INTEGER)")
    connection.commit()
    connection.close()
    before = target.read_bytes()
    assert main(["migrate"]) == EXIT_DATABASE
    assert "schema version is unknown" in capsys.readouterr().err
    assert target.read_bytes() == before


def test_migrate_reports_a_wrong_key_with_its_own_exit_code(target, capsys):
    main(["migrate"])
    connection = sqlite3.connect(target)
    connection.execute(
        "INSERT INTO user_credential (user_id, provider, file_path, ciphertext) VALUES ('u1', 'claude', 'f', 'not-a-valid-token')"
    )
    connection.commit()
    connection.close()
    assert main(["migrate"]) == EXIT_KEY_MISMATCH
    assert "cannot be decrypted" in capsys.readouterr().err


def test_the_server_will_not_start_on_a_database_that_was_never_migrated(target, ui, capsys):
    code = main(["--ui-dir", str(ui)], {})
    assert code == EXIT_DATABASE
    assert "python -m app.server migrate" in capsys.readouterr().err
    assert not target.exists()  # looking did not create it


def test_the_server_will_not_start_on_a_database_that_is_behind(target, ui, capsys):
    main(["migrate"])
    run_alembic(target, "downgrade", one_revision_back())
    capsys.readouterr()
    assert main(["--ui-dir", str(ui)], {}) == EXIT_DATABASE
    assert "older version" in capsys.readouterr().err
