"""Upgrade the schema shipped by 5233701, with synthetic history and credentials.

Its Alembic head was 4e8a1c7d9b30. Build that frozen revision at test time so
no private database, transcript or usable credential needs to be checked in.
"""
import sqlite3

from cryptography.fernet import Fernet

from tests.db.sqlite_helpers import run_alembic
from tests.desktop.test_desktop_entry import KEY, get, launch, launches  # noqa: F401
from app.services.workspaces import canonical_workspace

OLD_HEAD = "4e8a1c7d9b30"


def historical_fixture(database):
    result = run_alembic(database, "upgrade", OLD_HEAD)
    assert result.returncode == 0, result.stderr
    ciphertext = Fernet(KEY.encode()).encrypt(b"synthetic-test-credential").decode()
    with sqlite3.connect(database) as db:
        for number, status in enumerate(("succeeded", "interrupted", "cancelled"), 1):
            db.execute("INSERT INTO thread(id,owner_id,title,provider,provider_session_id,session_state,last_seq) VALUES (?, 'local-owner', 'fixture', 'claude', ?, 'active', 1)", (number, f"fixture-{number}"))
            db.execute("INSERT INTO turn(id,thread_id,seq,prompt_text,status,result) VALUES (?, ?, 1, 'fixture prompt', ?, 'fixture answer')", (number, number, status))
            db.execute("INSERT INTO event(thread_id,turn_id,seq,type,data) VALUES (?, ?, 1, 'assistant.message', '{\"text\":\"fixture answer\"}')", (number, number))
            db.execute("INSERT INTO approval(id,thread_id,turn_id,request_id,tool,input,status) VALUES (?, ?, ?, 'q', 'Write', '{}', 'cancelled')", (f"fixture-{number}", number, number))
        db.execute("INSERT INTO user_credential(user_id,provider,file_path,ciphertext) VALUES ('fixture','github','/fixture',?)", (ciphertext,))
    return ciphertext


# Columns later migrations add (empty for these old rows) are left out, so the
# comparison is about the history that existed before the upgrade.
ADDED_LATER = {"turn": {"engine_phase", "origin"}, "approval": {"consumed_at"}}


def history(database):
    with sqlite3.connect(database) as db:
        out = {}
        for name in ("turn", "event", "approval", "user_credential"):
            columns = [row[1] for row in db.execute(f'PRAGMA table_info("{name}")') if row[1] not in ADDED_LATER.get(name, ())]
            out[name] = db.execute(f'SELECT {", ".join(columns)} FROM "{name}" ORDER BY id').fetchall()
        return out


def test_existing_desktop_history_is_backed_up_upgraded_and_survives_restart(launches, tmp_path):
    database = tmp_path / "data" / "open-dots.db"
    ciphertext = historical_fixture(database)
    before = history(database)
    first = launch(launches, tmp_path).start()
    port = first.ready()["port"]
    threads = get(port, "/api/v1/threads").json()
    expected = str(canonical_workspace(str(tmp_path / "data" / "local")))
    assert len(threads) == 3
    assert all(thread["workspace_path"] == expected for thread in threads)
    assert first.stop() == 0
    assert history(database) == before
    with sqlite3.connect(database) as db:
        assert Fernet(KEY.encode()).decrypt(db.execute("SELECT ciphertext FROM user_credential").fetchone()[0].encode()) == b"synthetic-test-credential"
    backups = list((tmp_path / "data" / "backups").glob("*.db"))
    assert len(backups) == 1 and OLD_HEAD in backups[0].name
    assert history(backups[0]) == before
    second = launch(launches, tmp_path).start()
    assert get(second.ready()["port"], "/api/v1/threads").json() == threads
    assert second.stop() == 0
    assert history(database) == before
    assert len(list((tmp_path / "data" / "backups").glob("*.db"))) == 1


def test_workspace_backfill_can_finish_after_an_interrupted_sqlite_ddl(tmp_path):
    database = tmp_path / "partial.db"
    historical_fixture(database)
    # Model the crash boundary: SQLite persisted ADD COLUMN but not the stamp.
    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE thread ADD COLUMN workspace_path VARCHAR")
        db.execute("UPDATE thread SET workspace_path = ? WHERE id = 1", (str(tmp_path / "already-pinned"),))
    before = history(database)
    result = run_alembic(database, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT workspace_path FROM thread WHERE id = 1").fetchone()[0] == str(tmp_path / "already-pinned")
        assert db.execute("SELECT count(*) FROM thread WHERE workspace_path IS NULL").fetchone()[0] == 0
    assert history(database) == before
