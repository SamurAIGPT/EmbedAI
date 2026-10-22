"""The PostgreSQL -> SQLite copy tool (app/db/copy_to_sqlite.py).

Tests that need PostgreSQL build a throwaway database on the configured server
(like test_schema_equivalence.py) and are skipped when none is reachable. Set
REQUIRE_POSTGRES=1 (CI does) to fail instead of skipping. The refusal and
publication tests need no server.
"""

import os
import sqlite3
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.crypto import encrypt
from app.db import copy_to_sqlite as tool
from app.db.base import Base
from app.services import task_execution_service
from tests.db.sqlite_helpers import BACKEND_DIR

SECRET_PROMPT = "prompt-text-that-must-never-be-printed"
PLAINTEXT_CREDENTIAL = "plaintext-credential-that-must-never-be-printed"


def _skip_or_fail(reason: str):
    if os.environ.get("REQUIRE_POSTGRES") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


# --------------------------------------------------------------------------
# Fixtures


@pytest.fixture(scope="module")
def pg_url():
    """A scratch PostgreSQL database at the current migration head."""
    base = make_url(settings.POSTGRES_SYNC_URL)
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        admin.dispose()
        _skip_or_fail(f"no PostgreSQL server reachable: {type(exc).__name__}")
    name = f"od_copy_{uuid4().hex[:10]}"
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # noqa: BLE001
        admin.dispose()
        _skip_or_fail(f"cannot create a scratch database: {type(exc).__name__}")
    url = base.set(database=name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            env={**os.environ, "DATABASE_BACKEND": "postgres", "POSTGRES_DB": name, "PYTHONUTF8": "1"},
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        yield url
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _table(name: str):
    return Base.metadata.tables[name]


@pytest.fixture
def seeded(pg_url):
    """Fresh sample rows in the scratch database. Ids are not contiguous (a
    row is deleted) and timestamps carry microseconds and a non-UTC offset."""
    engine = create_engine(pg_url)
    instant = datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    with engine.begin() as c:
        c.execute(text("TRUNCATE " + ", ".join(Base.metadata.tables) + " RESTART IDENTITY CASCADE"))
        for n in range(3):
            c.execute(_table("schedule").insert().values(
                user_id="u1", provider="claude", box_id="b", prompt_text=SECRET_PROMPT,
                cron_expression="* * * * *", next_run_at=instant, is_active=bool(n % 2),
            ))
        c.execute(_table("schedule").delete().where(_table("schedule").c.id == 2))
        c.execute(_table("task").insert().values(
            user_id="u1", provider="claude", box_id="b", prompt_text=SECRET_PROMPT,
            status="succeeded", schedule_id=3, result="r", created_at=instant, updated_at=instant,
        ))
        c.execute(_table("user_sandbox").insert().values(user_id="u1", box_id="box-1", expires_at=instant))
        c.execute(_table("user_credential").insert().values(
            user_id="u1", provider="claude", file_path="/x", ciphertext=encrypt(PLAINTEXT_CREDENTIAL),
        ))
        c.execute(_table("permission_rule").insert().values(user_id="u1", bucket="system", decision="allow"))
        c.execute(_table("permission_ask").insert().values(
            task_id=1, user_id="u1", bucket="system", status="answered", decision="allow",
        ))
        c.execute(_table("link_code").insert().values(code="abc", expires_at=instant))
    engine.dispose()
    return {"url": pg_url, "instant": instant}


def run_tool(url, target: Path, **kwargs):
    messages: list[str] = []
    tool.copy_postgres_to_sqlite(url, target, out=messages.append, **kwargs)
    return "\n".join(messages)


def counts(url) -> dict[str, int]:
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return {n: c.execute(text(f'SELECT count(*) FROM "{n}"')).scalar_one() for n in Base.metadata.tables}
    finally:
        engine.dispose()


def only(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


# --------------------------------------------------------------------------
# The copy itself (needs PostgreSQL)


def test_copy_keeps_rows_ids_timestamps_and_credentials(seeded, tmp_path):
    before = counts(seeded["url"])
    target = tmp_path / "copy.db"
    output = run_tool(seeded["url"], target)

    assert only(tmp_path) == ["copy.db"]  # no temporary or side files left
    assert counts(seeded["url"]) == before  # PostgreSQL untouched

    lite = sqlite3.connect(target)
    try:
        for name, expected in before.items():
            assert lite.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] == expected
        assert [r[0] for r in lite.execute("SELECT id FROM schedule ORDER BY id")] == [1, 3]
        stored = lite.execute("SELECT created_at FROM task").fetchone()[0]
        assert datetime.fromisoformat(stored).replace(tzinfo=UTC) == seeded["instant"].astimezone(UTC)
        assert lite.execute("SELECT version_num FROM alembic_version").fetchall() == [(tool.alembic_head(),)]
        ciphertext = lite.execute("SELECT ciphertext FROM user_credential").fetchone()[0]
    finally:
        lite.close()
    from app.core.crypto import decrypt

    assert decrypt(ciphertext) == PLAINTEXT_CREDENTIAL
    assert SECRET_PROMPT not in output and PLAINTEXT_CREDENTIAL not in output


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_copied_file_is_private_to_the_owner(seeded, tmp_path):
    target = tmp_path / "copy.db"
    run_tool(seeded["url"], target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_dry_run_reports_counts_and_creates_nothing(seeded, tmp_path):
    target = tmp_path / "new-dir" / "copy.db"
    output = run_tool(seeded["url"], target, dry_run=True)
    assert not target.parent.exists()
    assert "schedule: 2 rows" in output and "task: 1 rows" in output
    assert SECRET_PROMPT not in output and PLAINTEXT_CREDENTIAL not in output


def test_source_is_one_read_only_repeatable_read_snapshot(seeded, tmp_path, monkeypatch):
    seen = {}
    real = tool.copy_tables

    def spy(source, destination):
        seen["read_only"] = source.execute(text("SHOW transaction_read_only")).scalar()
        seen["isolation"] = source.execute(text("SHOW transaction_isolation")).scalar()
        return real(source, destination)

    monkeypatch.setattr(tool, "copy_tables", spy)
    run_tool(seeded["url"], tmp_path / "copy.db")
    assert seen == {"read_only": "on", "isolation": "repeatable read"}


def test_refuses_while_a_task_is_active(seeded, tmp_path):
    engine = create_engine(seeded["url"])
    with engine.begin() as c:
        c.execute(text("UPDATE task SET status = 'running'"))
    engine.dispose()
    with pytest.raises(tool.MigrationRefused, match="still active"):
        run_tool(seeded["url"], tmp_path / "copy.db")
    assert only(tmp_path) == []
    run_tool(seeded["url"], tmp_path / "copy.db", allow_active=True)
    assert only(tmp_path) == ["copy.db"]


def test_refuses_while_another_connection_is_open(seeded, tmp_path):
    other = create_engine(seeded["url"])
    with other.connect() as c:
        c.execute(text("SELECT 1"))
        with pytest.raises(tool.MigrationRefused, match="other connection"):
            run_tool(seeded["url"], tmp_path / "copy.db")
        assert only(tmp_path) == []
    other.dispose()


def test_refuses_a_schema_that_is_not_at_head(seeded, tmp_path):
    engine = create_engine(seeded["url"])
    with engine.begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = 'old'"))
    engine.dispose()
    try:
        with pytest.raises(tool.MigrationRefused, match="not the current head"):
            run_tool(seeded["url"], tmp_path / "copy.db")
        assert only(tmp_path) == []
    finally:
        engine = create_engine(seeded["url"])
        with engine.begin() as c:
            c.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": tool.alembic_head()})
        engine.dispose()


def test_a_credential_that_cannot_be_decrypted_aborts(seeded, tmp_path):
    engine = create_engine(seeded["url"])
    with engine.begin() as c:
        c.execute(text("UPDATE user_credential SET ciphertext = :v"), {"v": Fernet(Fernet.generate_key()).encrypt(b"x").decode()})
    engine.dispose()
    with pytest.raises(tool.MigrationRefused, match="cannot be decrypted"):
        run_tool(seeded["url"], tmp_path / "copy.db")
    assert only(tmp_path) == []
    with pytest.raises(tool.MigrationRefused, match="cannot be decrypted"):
        run_tool(seeded["url"], tmp_path / "copy.db", dry_run=True)


def test_a_failed_verification_leaves_no_file(seeded, tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "verify_copy", lambda s, d: ["task: 1 row(s) differ"])
    with pytest.raises(tool.MigrationRefused, match="Verification failed"):
        run_tool(seeded["url"], tmp_path / "copy.db")
    assert only(tmp_path) == []


def test_an_error_while_copying_leaves_no_file(seeded, tmp_path, monkeypatch):
    def boom(source, destination):
        raise RuntimeError("disk full")

    monkeypatch.setattr(tool, "copy_tables", boom)
    with pytest.raises(RuntimeError):
        run_tool(seeded["url"], tmp_path / "copy.db")
    assert only(tmp_path) == []


def test_a_target_that_appears_while_copying_is_not_overwritten(seeded, tmp_path, monkeypatch):
    target = tmp_path / "copy.db"
    real = tool.finalize_sqlite

    def finalize_then_someone_creates_the_target(engine, temp):
        real(engine, temp)
        target.write_bytes(b"someone else's file")

    monkeypatch.setattr(tool, "finalize_sqlite", finalize_then_someone_creates_the_target)
    with pytest.raises(tool.MigrationRefused, match="appeared while copying"):
        run_tool(seeded["url"], target)
    assert target.read_bytes() == b"someone else's file"
    assert only(tmp_path) == ["copy.db"]  # the temporary file was cleaned up


# --------------------------------------------------------------------------
# No PostgreSQL needed


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_refuses_an_existing_target_and_leaves_it_alone(tmp_path, suffix):
    existing = tmp_path / f"copy.db{suffix}"
    existing.write_bytes(b"precious")
    with pytest.raises(tool.MigrationRefused, match="never overwrites"):
        run_tool("postgresql+psycopg2://u:p@127.0.0.1:1/none", tmp_path / "copy.db")
    assert existing.read_bytes() == b"precious"
    assert only(tmp_path) == [existing.name]


UNREACHABLE = "postgresql+psycopg2://u:p@127.0.0.1:1/none"


def refusal(target: Path) -> str:
    with pytest.raises(tool.MigrationRefused) as info:
        run_tool(UNREACHABLE, target)
    return str(info.value)


def test_refusal_says_the_existing_file_is_empty(tmp_path):
    (tmp_path / "copy.db").write_bytes(b"")
    assert "an empty file" in refusal(tmp_path / "copy.db")


def test_refusal_says_the_existing_file_is_an_open_dots_database_with_its_task_count(tmp_path):
    target = tmp_path / "copy.db"
    lite = sqlite3.connect(target)
    lite.execute("CREATE TABLE task (id INTEGER PRIMARY KEY, prompt_text TEXT)")
    lite.execute("CREATE TABLE alembic_version (version_num TEXT)")
    lite.executemany("INSERT INTO task (prompt_text) VALUES (?)", [(SECRET_PROMPT,)] * 3)
    lite.commit()
    lite.close()
    message = refusal(target)
    assert "an Open Duet database that already holds 3 task(s)" in message
    assert SECRET_PROMPT not in message


def test_refusal_says_the_existing_file_is_not_an_open_dots_database(tmp_path):
    target = tmp_path / "copy.db"
    lite = sqlite3.connect(target)
    lite.execute("CREATE TABLE other (id INTEGER)")
    lite.commit()
    lite.close()
    assert "not an Open Duet database" in refusal(target)


def test_refusal_says_the_existing_file_is_not_sqlite(tmp_path):
    (tmp_path / "copy.db").write_bytes(b"this is not a database" * 50)
    assert "not a SQLite database" in refusal(tmp_path / "copy.db")


def test_refusal_explains_side_files_and_names_them(tmp_path):
    (tmp_path / "copy.db").write_bytes(b"")
    (tmp_path / "copy.db-wal").write_bytes(b"x")
    (tmp_path / "copy.db-shm").write_bytes(b"x")
    message = refusal(tmp_path / "copy.db")
    assert "copy.db-wal, copy.db-shm" in message and "write-ahead-log" in message
    assert "stop the backend" in message


def test_refusal_for_side_files_without_a_database_file(tmp_path):
    (tmp_path / "copy.db-wal").write_bytes(b"x")
    message = refusal(tmp_path / "copy.db")
    assert "without a database file" in message and "copy.db-wal" in message


def test_inspecting_the_existing_file_changes_nothing(tmp_path):
    target = tmp_path / "copy.db"
    lite = sqlite3.connect(target)
    lite.execute("CREATE TABLE task (id INTEGER)")
    lite.execute("CREATE TABLE alembic_version (version_num TEXT)")
    lite.commit()
    lite.close()
    before = target.read_bytes()
    refusal(target)
    assert target.read_bytes() == before and only(tmp_path) == ["copy.db"]


def test_inspecting_a_wal_database_leaves_its_data_files_alone(tmp_path):
    """The -shm file is SQLite's shared-memory index (rebuilt on demand) and may
    be touched by any reader; the database and its write-ahead log must not be."""
    target = tmp_path / "copy.db"
    lite = sqlite3.connect(target)
    lite.execute("PRAGMA journal_mode=WAL")
    lite.execute("CREATE TABLE task (id INTEGER)")
    lite.execute("CREATE TABLE alembic_version (version_num TEXT)")
    lite.execute("INSERT INTO task VALUES (1)")
    lite.commit()
    try:
        before = {name: (tmp_path / name).read_bytes() for name in ("copy.db", "copy.db-wal")}
        assert "already holds 1 task(s)" in refusal(target)
        assert {name: (tmp_path / name).read_bytes() for name in before} == before
    finally:
        lite.close()


def test_an_unreachable_server_is_reported_without_the_password(tmp_path):
    with pytest.raises(tool.MigrationRefused, match="Cannot connect") as info:
        run_tool("postgresql+psycopg2://user:hunter2@127.0.0.1:1/none", tmp_path / "copy.db")
    assert "hunter2" not in str(info.value)
    assert only(tmp_path) == []


def test_publish_never_overwrites(tmp_path):
    temp, target = tmp_path / "temp", tmp_path / "target"
    temp.write_bytes(b"new")
    target.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        tool.publish_no_overwrite(temp, target)
    assert target.read_bytes() == b"old" and temp.read_bytes() == b"new"


def test_publish_moves_the_file(tmp_path):
    temp, target = tmp_path / "temp", tmp_path / "target"
    temp.write_bytes(b"new")
    tool.publish_no_overwrite(temp, target)
    assert target.read_bytes() == b"new" and not temp.exists()


def test_publish_without_hard_links_falls_back_to_rename(tmp_path, monkeypatch):
    def no_links(*args, **kwargs):
        raise PermissionError("hard links not supported")

    monkeypatch.setattr(os, "link", no_links)
    temp, target = tmp_path / "temp", tmp_path / "target"
    temp.write_bytes(b"new")
    tool.publish_no_overwrite(temp, target)
    assert target.read_bytes() == b"new" and not temp.exists()
    temp.write_bytes(b"newer")
    with pytest.raises(FileExistsError):
        tool.publish_no_overwrite(temp, target)
    assert target.read_bytes() == b"new"


def test_active_statuses_match_the_service():
    assert tool.ACTIVE_TASK_STATUSES == task_execution_service.ACTIVE_STATUSES


def test_the_tool_never_changes_settings(seeded, tmp_path):
    """Copying and switching are separate steps: .env is left exactly as it was."""
    env_file = BACKEND_DIR / ".env"
    before = env_file.read_bytes() if env_file.exists() else None
    run_tool(seeded["url"], tmp_path / "copy.db")
    assert (env_file.read_bytes() if env_file.exists() else None) == before
