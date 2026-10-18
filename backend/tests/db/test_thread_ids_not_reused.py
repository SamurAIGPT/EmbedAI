"""A deleted conversation's id is never given to a new one on SQLite.

SQLite hands out max(id) + 1 for a plain INTEGER PRIMARY KEY, so deleting the
newest conversation used to give its id to the next one: a saved link
(/chat?thread=N) and decision-log or sentinel entries for the deleted
conversation then pointed at a different one. The thread, turn and event tables
are declared AUTOINCREMENT, which never reuses an id. PostgreSQL identity
columns never reused ids, so nothing changes there.

Plain `sqlite3` with foreign keys on, the way the app opens the file."""

import json
import sqlite3

import pytest
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable

from app.db import models  # noqa: F401
from app.db.base import Base
from app.db.engine import create_app_sync_engine
from tests.db.schema_description import describe
from tests.db.sqlite_helpers import run_alembic

# The revision before thread ids became AUTOINCREMENT on SQLite.
BEFORE = "c9bc6a5c0df4"
MONOTONIC_TABLES = ("thread", "turn", "event")
REBUILT_TABLES = ("thread", "turn", "event", "approval")


def connect(path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def upgrade(path, target="head"):
    result = run_alembic(path, "upgrade", target)
    assert result.returncode == 0, result.stderr


def schema_of(path):
    engine = create_app_sync_engine(f"sqlite:///{path.as_posix()}")
    try:
        return describe(engine)
    finally:
        engine.dispose()


def add_thread(connection, owner="o") -> int:
    return connection.execute("INSERT INTO thread (owner_id, provider) VALUES (?, 'claude')", (owner,)).lastrowid


def add_turn(connection, thread_id, seq=1, status="succeeded") -> int:
    return connection.execute(
        "INSERT INTO turn (thread_id, seq, prompt_text, status) VALUES (?, ?, 'p', ?)", (thread_id, seq, status)
    ).lastrowid


def add_event(connection, thread_id, turn_id, seq=1) -> int:
    return connection.execute(
        "INSERT INTO event (thread_id, turn_id, seq, type, data) VALUES (?, ?, ?, 'x', '{\"k\": 1}')",
        (thread_id, turn_id, seq),
    ).lastrowid


def add_conversation(connection, label: str) -> tuple[int, int, int]:
    thread = add_thread(connection, owner=label)
    turn = add_turn(connection, thread)
    event = add_event(connection, thread, turn)
    connection.execute(
        "INSERT INTO approval (id, thread_id, turn_id, request_id, tool, input) VALUES (?, ?, ?, 'r', 'Bash', '{}')",
        (f"ap-{label}", thread, turn),
    )
    return thread, turn, event


def delete_conversation(connection, thread_id):
    for table in ("approval", "event", "turn"):
        connection.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))
    connection.execute("DELETE FROM thread WHERE id = ?", (thread_id,))


def columns_of(connection) -> dict:
    return {
        table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        for table in REBUILT_TABLES
    }


def all_rows(connection, columns: dict | None = None) -> dict:
    """Every row of the rebuilt tables; with `columns`, only those columns
    (the ones a table had before later migrations added more)."""
    return {
        table: connection.execute(
            f"SELECT {', '.join(columns[table]) if columns else '*'} FROM {table} ORDER BY id"
        ).fetchall()
        for table in REBUILT_TABLES
    }


def index_sql(connection) -> dict:
    return dict(connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name IN "
        "('thread', 'turn', 'event', 'approval') AND sql IS NOT NULL"
    ).fetchall())


def table_sql(connection, table) -> str:
    return connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()[0]


def assert_never_reused(connection):
    """Delete the newest conversation, then start another: every id is higher."""
    thread, turn, event = add_conversation(connection, "newest")
    delete_conversation(connection, thread)
    again, again_turn, again_event = add_conversation(connection, "next")
    assert again > thread
    assert again_turn > turn
    assert again_event > event


# --- a new database ------------------------------------------------------------


def test_the_models_declare_autoincrement_for_sqlite_only():
    for table in MONOTONIC_TABLES:
        ddl = str(CreateTable(Base.metadata.tables[table]).compile(dialect=sqlite.dialect()))
        assert "AUTOINCREMENT" in ddl, table
    approval = str(CreateTable(Base.metadata.tables["approval"]).compile(dialect=sqlite.dialect()))
    assert "AUTOINCREMENT" not in approval  # its id is a string, never assigned by the database


def test_a_new_database_never_reuses_a_deleted_conversations_ids(tmp_path):
    path = tmp_path / "new.db"
    upgrade(path)
    connection = connect(path)
    try:
        for table in MONOTONIC_TABLES:
            assert "AUTOINCREMENT" in table_sql(connection, table), table
        assert_never_reused(connection)
        connection.commit()
    finally:
        connection.close()


# --- an existing database --------------------------------------------------------


@pytest.fixture
def existing(tmp_path):
    """A database at the revision before the change, with three conversations."""
    path = tmp_path / "existing.db"
    upgrade(path, BEFORE)
    connection = connect(path)
    for label in ("a", "b", "c"):
        add_conversation(connection, label)
    connection.execute("UPDATE thread SET provider_session_id = 's-a', provider_state = '{\"x\": [1, 2]}' WHERE id = 1")
    connection.commit()
    connection.close()
    return path


def test_upgrading_keeps_every_row_index_and_foreign_key(existing, tmp_path):
    connection = connect(existing)
    rows_before, indexes_before = all_rows(connection), index_sql(connection)
    columns_before = columns_of(connection)
    connection.close()

    upgrade(existing)

    connection = connect(existing)
    try:
        assert all_rows(connection, columns_before) == rows_before
        assert index_sql(connection) == indexes_before
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert not [n for (n,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%rebuild%'"
        )]  # no scratch tables left behind
        for table in MONOTONIC_TABLES:
            assert "AUTOINCREMENT" in table_sql(connection, table), table
        # The rules the database enforces still hold.
        with pytest.raises(sqlite3.IntegrityError):
            add_turn(connection, 999)
        with pytest.raises(sqlite3.IntegrityError):
            add_turn(connection, 1, seq=1)
    finally:
        connection.close()
    fresh = tmp_path / "fresh.db"
    upgrade(fresh)
    assert schema_of(existing) == schema_of(fresh)


def test_an_upgraded_database_never_reuses_a_deleted_conversations_ids(existing):
    upgrade(existing)
    connection = connect(existing)
    try:
        delete_conversation(connection, 3)  # the newest, from before the upgrade
        assert add_thread(connection) == 4
        assert_never_reused(connection)
        connection.commit()
    finally:
        connection.close()


def test_an_id_the_decision_log_still_names_is_not_handed_out(existing, decision_log_dir):
    """Conversation 4 was deleted before the upgrade, so the database no longer
    knows it existed; its decision-log entries do. The next id is 5, so those
    entries (and the sentinel's view of them) never describe a new conversation."""
    connection = connect(existing)
    four, _turn, _event = add_conversation(connection, "d")
    delete_conversation(connection, four)
    connection.commit()
    connection.close()
    decision_log_dir.mkdir(parents=True)
    (decision_log_dir / "decisions-2026-10.ndjson").write_text(
        json.dumps({"id": "1", "event": "denied", "thread_id": four}) + "\nnot json\n"
        + json.dumps({"id": "2", "event": "shown", "thread_id": "x"}) + "\n",
        encoding="utf-8",
    )

    upgrade(existing)

    connection = connect(existing)
    try:
        assert add_thread(connection) == 5
    finally:
        connection.close()


def test_the_decision_log_is_not_read_for_a_database_without_conversations(tmp_path, decision_log_dir):
    decision_log_dir.mkdir(parents=True)
    (decision_log_dir / "decisions-2026-10.ndjson").write_text(
        json.dumps({"id": "1", "event": "denied", "thread_id": 50}) + "\n", encoding="utf-8"
    )
    path = tmp_path / "new.db"
    upgrade(path)
    connection = connect(path)
    try:
        assert add_thread(connection) == 1
    finally:
        connection.close()


def test_downgrade_and_upgrade_again_keep_the_data(existing):
    connection = connect(existing)
    # The columns this migration's era knew; later migrations add more.
    columns = columns_of(connection)
    connection.close()
    upgrade(existing)
    connection = connect(existing)
    rows = all_rows(connection, columns)
    connection.close()

    down = run_alembic(existing, "downgrade", BEFORE)
    assert down.returncode == 0, down.stderr
    connection = connect(existing)
    assert all_rows(connection, columns) == rows
    assert "AUTOINCREMENT" not in table_sql(connection, "thread")
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()

    upgrade(existing)
    connection = connect(existing)
    try:
        assert all_rows(connection, columns) == rows
        assert "AUTOINCREMENT" in table_sql(connection, "thread")
    finally:
        connection.close()


def test_a_table_this_migration_does_not_know_about_stops_it_unchanged(existing):
    """Rebuilding thread would break a foreign key from a table added outside
    this migration chain, so the upgrade refuses and changes nothing."""
    connection = connect(existing)
    connection.execute("CREATE TABLE stranger (id INTEGER PRIMARY KEY, thread_id BIGINT REFERENCES thread (id))")
    connection.execute("INSERT INTO stranger (thread_id) VALUES (1)")
    connection.commit()
    rows = all_rows(connection)
    connection.close()

    result = run_alembic(existing, "upgrade", "head")

    assert result.returncode != 0
    assert "stranger" in result.stderr
    connection = connect(existing)
    try:
        assert all_rows(connection) == rows
        assert "AUTOINCREMENT" not in table_sql(connection, "thread")
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == BEFORE
    finally:
        connection.close()
