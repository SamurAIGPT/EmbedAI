"""The SQLite schema at Alembic revision d2fc641cb3d1, frozen.

The existing Alembic chain was written for PostgreSQL and does not run on
SQLite (it alters constraints on existing tables and uses `now()` defaults).
A new SQLite database is therefore created from this baseline and stamped at
BASELINE_REVISION; every later migration then runs on top of it like on
PostgreSQL. Keep the migrations after that revision SQLite-safe (additive, or
`op.batch_alter_table`).

The thread tables are not in the baseline: they come from later migrations.
`thread`, `turn` and `event` are AUTOINCREMENT on SQLite (8d5f3a2b6c74), so ids
are never reused. A batch rebuild of one of them must pass
`table_kwargs={"sqlite_autoincrement": True}` to keep that, and cannot drop
`thread` or `turn` while other tables hold foreign keys to them (foreign keys
are on); 8d5f3a2b6c74 shows a rebuild that works.

This is deliberately a literal snapshot, not generated from the models: the
models change over time and the baseline must not. tests/db/test_sqlite_schema.py
checks that a database upgraded to the latest revision still matches the models.
"""

import sqlite3
from pathlib import Path

from sqlalchemy.engine import Connection

BASELINE_REVISION = "d2fc641cb3d1"

BASELINE_TABLES = frozenset({
    "link_code", "permission_ask", "permission_rule", "schedule",
    "task", "user_credential", "user_sandbox",
})


class SqliteNotReady(RuntimeError):
    """The SQLite file is not something this app created (or is half-made)."""

_BASELINE_SQL = """
CREATE TABLE link_code (
    id INTEGER NOT NULL,
    code VARCHAR NOT NULL,
    user_id VARCHAR,
    expires_at DATETIME NOT NULL,
    claimed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_link_code_code ON link_code (code);

CREATE INDEX ix_link_code_user_id ON link_code (user_id);

CREATE TABLE permission_rule (
    id INTEGER NOT NULL,
    user_id VARCHAR NOT NULL,
    bucket VARCHAR NOT NULL,
    connector VARCHAR,
    tool VARCHAR,
    decision VARCHAR NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_permission_rule_bucket ON permission_rule (bucket);

CREATE INDEX ix_permission_rule_lookup ON permission_rule (user_id, bucket, connector, tool);

CREATE INDEX ix_permission_rule_user_id ON permission_rule (user_id);

CREATE TABLE schedule (
    id INTEGER NOT NULL,
    user_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    box_id VARCHAR NOT NULL,
    prompt_text TEXT NOT NULL,
    cron_expression VARCHAR NOT NULL,
    timezone VARCHAR NOT NULL,
    is_active BOOLEAN NOT NULL,
    next_run_at DATETIME NOT NULL,
    last_run_at DATETIME,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL,
    paused_reason TEXT,
    deleted_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);

CREATE INDEX ix_schedule_next_run_at ON schedule (next_run_at);

CREATE INDEX ix_schedule_user_id ON schedule (user_id);

CREATE TABLE user_credential (
    id INTEGER NOT NULL,
    user_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    file_path VARCHAR NOT NULL,
    ciphertext TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_user_credential_user_provider UNIQUE (user_id, provider)
);

CREATE INDEX ix_user_credential_user_id ON user_credential (user_id);

CREATE TABLE user_sandbox (
    id INTEGER NOT NULL,
    user_id VARCHAR NOT NULL,
    box_id VARCHAR NOT NULL,
    state VARCHAR,
    machine_type VARCHAR,
    ttl_seconds INTEGER NOT NULL,
    expires_at DATETIME,
    is_active BOOLEAN NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (box_id)
);

CREATE INDEX ix_user_sandbox_is_active ON user_sandbox (is_active);

CREATE INDEX ix_user_sandbox_user_id ON user_sandbox (user_id);

CREATE TABLE task (
    id INTEGER NOT NULL,
    user_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    box_id VARCHAR NOT NULL,
    prompt_text TEXT NOT NULL,
    status VARCHAR NOT NULL,
    prompt_id VARCHAR,
    session_id VARCHAR,
    schedule_id BIGINT,
    scheduled_for DATETIME,
    result TEXT,
    reported_at DATETIME,
    error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_task_schedule_id_scheduled_for UNIQUE (schedule_id, scheduled_for),
    CONSTRAINT fk_task_schedule_id_schedule FOREIGN KEY(schedule_id) REFERENCES schedule (id)
);

CREATE INDEX ix_task_schedule_id ON task (schedule_id);

CREATE INDEX ix_task_session_id ON task (session_id);

CREATE INDEX ix_task_status ON task (status);

CREATE INDEX ix_task_user_id ON task (user_id);

CREATE TABLE permission_ask (
    id INTEGER NOT NULL,
    task_id BIGINT NOT NULL,
    user_id VARCHAR NOT NULL,
    bucket VARCHAR NOT NULL,
    connector VARCHAR,
    tool VARCHAR,
    status VARCHAR NOT NULL,
    decision VARCHAR,
    always BOOLEAN NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    answered_at DATETIME,
    PRIMARY KEY (id),
    CONSTRAINT fk_permission_ask_task_id_task FOREIGN KEY(task_id) REFERENCES task (id)
);

CREATE INDEX ix_permission_ask_status ON permission_ask (status);

CREATE INDEX ix_permission_ask_task_id ON permission_ask (task_id);

CREATE INDEX ix_permission_ask_user_id ON permission_ask (user_id);
"""


def create_baseline(connection: Connection) -> None:
    for statement in _BASELINE_SQL.split(";"):
        statement = statement.strip()
        if statement:
            connection.exec_driver_sql(statement)


def sqlite_file_state(path: str | Path) -> str:
    """"new" for an empty file, "ready" for a database this app created.

    Anything else (tables but no version table, an empty version table, a
    missing baseline table, or a file that is not a SQLite database) raises
    SqliteNotReady. The file is opened read-only and with no settings applied:
    even turning on WAL mode rewrites a database file's header, and a database
    that is refused must be left exactly as it was.
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return "new"  # the file does not exist yet
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not tables:
            return "new"
        if "alembic_version" not in tables:
            raise SqliteNotReady(
                "This SQLite database has tables but no alembic_version table, so its "
                "schema version is unknown. Use a new database file or restore a backup."
            )
        if connection.execute("SELECT count(*) FROM alembic_version").fetchone()[0] == 0:
            raise SqliteNotReady(
                "This SQLite database has an empty alembic_version table, so it was not "
                "fully initialised. Use a new database file or restore a backup."
            )
        missing = sorted(BASELINE_TABLES - tables)
        if missing:
            raise SqliteNotReady(
                f"This SQLite database is missing tables ({', '.join(missing)}), so it was "
                "not fully initialised or was altered. Use a new database file or restore a backup."
            )
        return "ready"
    except sqlite3.DatabaseError as exc:
        raise SqliteNotReady(f"This file is not a usable SQLite database ({exc}).") from exc
    finally:
        connection.close()
