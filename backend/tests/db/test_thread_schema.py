"""The thread tables (F04) at the SQLite level: upgrading a database that already
has data, downgrading, and the rules the database itself enforces.

These use plain `sqlite3`, not the app's models, so they show what the database
refuses on its own, whatever code is writing to it."""

import sqlite3

import pytest

from tests.db.sqlite_helpers import run_alembic

BASELINE = "d2fc641cb3d1"
NEW_TABLES = {"thread", "turn", "event", "approval"}


def tables(path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        connection.close()


@pytest.fixture
def database(tmp_path):
    """A migrated SQLite file, opened the way the app opens it (foreign keys on)."""
    path = tmp_path / "threads.db"
    result = run_alembic(path, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    yield connection
    connection.close()


def add_thread(connection, provider="claude", session=None) -> int:
    cursor = connection.execute(
        "INSERT INTO thread (owner_id, provider, provider_session_id) VALUES ('o', ?, ?)", (provider, session)
    )
    return cursor.lastrowid


def add_turn(connection, thread_id, seq, status="running") -> int:
    cursor = connection.execute(
        "INSERT INTO turn (thread_id, seq, prompt_text, status) VALUES (?, ?, 'p', ?)", (thread_id, seq, status)
    )
    return cursor.lastrowid


def test_an_existing_database_with_data_upgrades_and_keeps_its_data(tmp_path):
    path = tmp_path / "existing.db"
    assert run_alembic(path, "upgrade", BASELINE).returncode == 0
    assert not NEW_TABLES & tables(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO task (user_id, provider, box_id, prompt_text, status) VALUES ('u', 'claude', 'b', 'keep me', 'queued')"
    )
    connection.commit()
    connection.close()

    upgrade = run_alembic(path, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    assert NEW_TABLES <= tables(path)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT prompt_text FROM task").fetchall() == [("keep me",)]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    connection.close()


def test_downgrade_removes_only_the_new_tables_and_upgrade_restores_them(tmp_path):
    path = tmp_path / "roundtrip.db"
    assert run_alembic(path, "upgrade", "head").returncode == 0
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO task (user_id, provider, box_id, prompt_text, status) VALUES ('u', 'claude', 'b', 'old task', 'queued')"
    )
    connection.commit()
    connection.close()

    down = run_alembic(path, "downgrade", BASELINE)
    assert down.returncode == 0, down.stderr
    assert not NEW_TABLES & tables(path)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT prompt_text FROM task").fetchall() == [("old task",)]
    connection.close()

    again = run_alembic(path, "upgrade", "head")
    assert again.returncode == 0, again.stderr
    assert NEW_TABLES <= tables(path)


def test_ids_are_assigned_by_the_database(database):
    """SQLite only auto-assigns an id for an INTEGER PRIMARY KEY column."""
    first = add_thread(database)
    second = add_thread(database)
    assert (first, second) == (1, 2)
    turn = add_turn(database, first, 1)
    event = database.execute(
        "INSERT INTO event (thread_id, turn_id, seq, type, data) VALUES (?, ?, 1, 'x', '{}')", (first, turn)
    ).lastrowid
    assert (turn, event) == (1, 1)


def test_the_database_allows_only_one_running_turn_per_thread(database):
    thread = add_thread(database)
    add_turn(database, thread, 1)
    with pytest.raises(sqlite3.IntegrityError):
        add_turn(database, thread, 2)  # a second running turn
    # Finished turns do not count, other threads are independent.
    database.execute("UPDATE turn SET status = 'succeeded' WHERE thread_id = ?", (thread,))
    add_turn(database, thread, 2)
    add_turn(database, add_thread(database), 1)


def test_turn_and_event_numbers_are_unique_within_a_thread(database):
    thread, other = add_thread(database), add_thread(database)
    add_turn(database, thread, 1, status="succeeded")
    with pytest.raises(sqlite3.IntegrityError):
        add_turn(database, thread, 1, status="succeeded")
    add_turn(database, other, 1, status="succeeded")  # the same number in another thread is fine

    database.execute("INSERT INTO event (thread_id, seq, type, data) VALUES (?, 1, 'x', '{}')", (thread,))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO event (thread_id, seq, type, data) VALUES (?, 1, 'y', '{}')", (thread,))


def test_a_provider_session_belongs_to_one_thread(database):
    add_thread(database, session="s1")
    with pytest.raises(sqlite3.IntegrityError):
        add_thread(database, session="s1")
    add_thread(database, provider="codex", session="s1")
    add_thread(database)
    add_thread(database)  # no session id: unrestricted


def test_children_cannot_point_at_nothing(database):
    with pytest.raises(sqlite3.IntegrityError):
        add_turn(database, 999, 1)
    thread = add_thread(database)
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO event (thread_id, turn_id, seq, type, data) VALUES (?, 999, 1, 'x', '{}')", (thread,))
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO approval (id, thread_id, turn_id, request_id, tool, input) VALUES ('a', ?, 999, 'r', 'Bash', '{}')",
            (thread,),
        )


def test_open_sets_carry_no_database_constraint(database):
    """A new provider, event type, approval kind or status word needs no migration."""
    thread = add_thread(database, provider="some-future-provider")
    database.execute("UPDATE thread SET session_state = 'something_new' WHERE id = ?", (thread,))
    database.execute("INSERT INTO event (thread_id, seq, type, data) VALUES (?, 1, 'brand.new.type', '{}')", (thread,))
    turn = add_turn(database, thread, 1, status="paused")
    database.execute(
        "INSERT INTO approval (id, thread_id, turn_id, request_id, tool, input, kind) VALUES ('a', ?, ?, 'r', 't', '{}', 'telepathy')",
        (thread, turn),
    )


def test_defaults_are_filled_in_by_the_database(database):
    thread = add_thread(database)
    row = database.execute(
        "SELECT session_state, last_seq, created_at, updated_at, archived_at, provider_state FROM thread WHERE id = ?",
        (thread,),
    ).fetchone()
    assert row[:2] == ("new", 0)
    assert row[2] and row[3] and row[4] is None and row[5] is None
    turn = add_turn(database, thread, 1)
    status, started = database.execute("SELECT status, started_at FROM turn WHERE id = ?", (turn,)).fetchone()
    assert status == "running" and started
