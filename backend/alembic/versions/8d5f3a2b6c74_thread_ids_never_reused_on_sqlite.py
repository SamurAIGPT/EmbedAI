"""Thread, turn and event ids are never reused on SQLite.

SQLite gives a plain INTEGER PRIMARY KEY the value max(id) + 1, so deleting the
newest conversation handed its id to the next one, and saved links and
decision-log entries then named a different conversation. AUTOINCREMENT never
reuses an id. PostgreSQL identity columns never did, so this does nothing there.

SQLite cannot add AUTOINCREMENT to an existing table, so the four thread tables
are rebuilt: copied aside, dropped (children first, so no foreign key is ever
broken while foreign keys stay on), created again, refilled (parents first)
with the same ids, and their indexes recreated. Everything runs in this
migration's one transaction: a failure leaves the database as it was. The
desktop app copies the database file aside before any pending migration.

The DDL is spelled out (not generated from the models) so this migration never
changes.

Revision ID: 8d5f3a2b6c74
Revises: c9bc6a5c0df4
"""

from alembic import context, op

revision = "8d5f3a2b6c74"
down_revision = "c9bc6a5c0df4"
branch_labels = None
depends_on = None

# Parents first: the order the tables are created and refilled in.
TABLES = ("thread", "turn", "event", "approval")
MONOTONIC = ("thread", "turn", "event")

_ID = "id INTEGER NOT NULL, "
_ID_AUTOINCREMENT = "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "

# {id} becomes the id column; {pk} the table-level primary key, which a column
# declared PRIMARY KEY AUTOINCREMENT must not repeat.
_TABLE_SQL = {
    "thread": """CREATE TABLE thread (
	{id}
	owner_id VARCHAR NOT NULL,
	title VARCHAR,
	provider VARCHAR NOT NULL,
	model VARCHAR,
	provider_session_id VARCHAR,
	session_state VARCHAR DEFAULT 'new' NOT NULL,
	provider_state JSON,
	last_seq BIGINT DEFAULT '0' NOT NULL,
	archived_at DATETIME,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	workspace_path VARCHAR,
	agent_id BIGINT{pk}
)""",
    "turn": """CREATE TABLE turn (
	{id}
	thread_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,
	prompt_text TEXT NOT NULL,
	status VARCHAR DEFAULT 'running' NOT NULL,
	error_code VARCHAR,
	result TEXT,
	error TEXT,
	model VARCHAR,
	usage JSON,
	owner_token VARCHAR,
	owner_instance VARCHAR,
	heartbeat_at DATETIME,
	started_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	finished_at DATETIME{pk},
	CONSTRAINT fk_turn_thread_id_thread FOREIGN KEY(thread_id) REFERENCES thread (id),
	CONSTRAINT uq_turn_thread_id_seq UNIQUE (thread_id, seq)
)""",
    "event": """CREATE TABLE event (
	{id}
	thread_id BIGINT NOT NULL,
	turn_id BIGINT,
	seq BIGINT NOT NULL,
	type VARCHAR NOT NULL,
	data JSON NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL{pk},
	CONSTRAINT fk_event_thread_id_thread FOREIGN KEY(thread_id) REFERENCES thread (id),
	CONSTRAINT fk_event_turn_id_turn FOREIGN KEY(turn_id) REFERENCES turn (id),
	CONSTRAINT uq_event_thread_id_seq UNIQUE (thread_id, seq)
)""",
    "approval": """CREATE TABLE approval (
	id VARCHAR NOT NULL,
	thread_id BIGINT NOT NULL,
	turn_id BIGINT NOT NULL,
	request_id VARCHAR NOT NULL,
	tool VARCHAR NOT NULL,
	input JSON NOT NULL,
	summary TEXT,
	kind VARCHAR,
	status VARCHAR DEFAULT 'pending' NOT NULL,
	delivered_at DATETIME,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	decided_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT fk_approval_thread_id_thread FOREIGN KEY(thread_id) REFERENCES thread (id),
	CONSTRAINT fk_approval_turn_id_turn FOREIGN KEY(turn_id) REFERENCES turn (id)
)""",
}

_COLUMNS = {
    "thread": (
        "id", "owner_id", "title", "provider", "model", "provider_session_id", "session_state",
        "provider_state", "last_seq", "archived_at", "created_at", "updated_at", "workspace_path", "agent_id",
    ),
    "turn": (
        "id", "thread_id", "seq", "prompt_text", "status", "error_code", "result", "error", "model",
        "usage", "owner_token", "owner_instance", "heartbeat_at", "started_at", "finished_at",
    ),
    "event": ("id", "thread_id", "turn_id", "seq", "type", "data", "created_at"),
    "approval": (
        "id", "thread_id", "turn_id", "request_id", "tool", "input", "summary", "kind", "status",
        "delivered_at", "created_at", "decided_at",
    ),
}

_INDEX_SQL = (
    "CREATE INDEX ix_thread_owner_listing ON thread (owner_id, archived_at, updated_at)",
    "CREATE UNIQUE INDEX uq_thread_provider_session ON thread (provider, provider_session_id) "
    "WHERE provider_session_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_turn_one_running_per_thread ON turn (thread_id) WHERE status = 'running'",
    "CREATE INDEX ix_turn_status_heartbeat ON turn (status, heartbeat_at)",
    "CREATE INDEX ix_event_turn_id ON event (turn_id)",
    "CREATE INDEX ix_approval_turn_id_status ON approval (turn_id, status)",
)


def _scratch(table: str) -> str:
    return f"_rebuild_{table}"


def _table_sql(table: str, autoincrement: bool) -> str:
    if table not in MONOTONIC:
        return _TABLE_SQL[table]
    if autoincrement:
        return _TABLE_SQL[table].format(id=_ID_AUTOINCREMENT, pk="")
    return _TABLE_SQL[table].format(id=_ID, pk=", \n\tPRIMARY KEY (id)")


def _check_nothing_else_points_here(bind) -> None:
    """Dropping these tables would break a foreign key from any other table."""
    names = [r[0] for r in bind.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )]
    for name in names:
        if name in TABLES:
            continue
        quoted = name.replace('"', '""')
        for row in bind.exec_driver_sql(f'PRAGMA foreign_key_list("{quoted}")'):
            if row[2] in TABLES:
                raise RuntimeError(
                    f"Table {name} has a foreign key to {row[2]}; this migration rebuilds "
                    f"{', '.join(TABLES)} and does not know how to keep that key. Nothing was changed."
                )


def _ids_named_in_decision_log() -> int:
    """The highest conversation id the decision log names, or 0. A conversation
    deleted before this upgrade left no trace in the database but may have log
    entries; starting above them keeps those entries from describing a new
    conversation. Best effort: an unreadable log is skipped."""
    try:
        from app.services import decision_log

        highest = 0
        for entry in decision_log.iter_entries():
            value = entry.get("thread_id")
            if isinstance(value, int) and not isinstance(value, bool):
                highest = max(highest, value)
        return highest
    except Exception:  # noqa: BLE001 - never block the upgrade on the log
        return 0


def _rebuild(autoincrement: bool) -> None:
    bind = op.get_bind()
    _check_nothing_else_points_here(bind)
    had_threads = bind.exec_driver_sql("SELECT EXISTS (SELECT 1 FROM thread)").scalar()

    for table in TABLES:
        columns = ", ".join(_COLUMNS[table])
        bind.exec_driver_sql(f"DROP TABLE IF EXISTS {_scratch(table)}")
        bind.exec_driver_sql(f"CREATE TABLE {_scratch(table)} AS SELECT {columns} FROM {table}")
    for table in reversed(TABLES):  # children first
        bind.exec_driver_sql(f"DROP TABLE {table}")
    for table in TABLES:  # parents first
        bind.exec_driver_sql(_table_sql(table, autoincrement))
        columns = ", ".join(_COLUMNS[table])
        bind.exec_driver_sql(
            f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {_scratch(table)} ORDER BY rowid"
        )
        bind.exec_driver_sql(f"DROP TABLE {_scratch(table)}")
    for statement in _INDEX_SQL:
        bind.exec_driver_sql(statement)

    if autoincrement and had_threads:
        floor = _ids_named_in_decision_log()
        current = bind.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name = 'thread'").scalar() or 0
        if floor > current:
            bind.exec_driver_sql("UPDATE sqlite_sequence SET seq = ? WHERE name = 'thread'", (floor,))


def upgrade():
    if context.is_offline_mode() or op.get_bind().dialect.name != "sqlite":
        return
    _rebuild(autoincrement=True)


def downgrade():
    if context.is_offline_mode() or op.get_bind().dialect.name != "sqlite":
        return
    _rebuild(autoincrement=False)
