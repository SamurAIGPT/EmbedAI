"""Routines, their run history, and the in-app notification feed.

New tables only, so the same on SQLite and PostgreSQL. On SQLite the ids are
AUTOINCREMENT from the start (runs and notifications name each other by id).

Revision ID: b5e3c8a1d702
Revises: 1f6c3a9e7d52
"""
from alembic import context, op
import sqlalchemy as sa

revision = "b5e3c8a1d702"
down_revision = "1f6c3a9e7d52"
branch_labels = None
depends_on = None

_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    # Each step checks first, so a re-run after a crash before the version
    # stamp completes the upgrade.
    offline = context.is_offline_mode()
    if offline or "routine" not in _tables():
        op.create_table(
            "routine",
            sa.Column("id", _ID, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("schedule_kind", sa.String(), nullable=False),
            sa.Column("schedule_spec", sa.JSON(none_as_null=True), nullable=False),
            sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
            sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("paused_reason", sa.Text(), nullable=True),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("run_limit_minutes", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("approval_mode", sa.String(), nullable=False, server_default="ask"),
            sa.Column("approval_timeout_minutes", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("catch_up_minutes", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("webhook_secret_hash", sa.String(), nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
    if offline or "ix_routine_paused_next_run_at" not in _indexes("routine"):
        op.create_index("ix_routine_paused_next_run_at", "routine", ["paused", "next_run_at"])

    if offline or "routine_run" not in _tables():
        op.create_table(
            "routine_run",
            sa.Column("id", _ID, sa.Identity(always=True), primary_key=True),
            sa.Column("routine_id", sa.BigInteger(), nullable=False),
            sa.Column("trigger", sa.String(), nullable=False),
            sa.Column("state", sa.String(), nullable=False, server_default="queued"),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
            sa.Column("prompt", sa.Text(), nullable=True),
            sa.Column("thread_id", sa.BigInteger(), nullable=True),
            sa.Column("turn_id", sa.BigInteger(), nullable=True),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
            sa.Column("waited_seconds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["routine_id"], ["routine.id"], name="fk_routine_run_routine_id_routine"),
            sqlite_autoincrement=True,
        )
    existing = set() if offline else _indexes("routine_run")
    if "ix_routine_run_routine_id_created_at" not in existing:
        op.create_index("ix_routine_run_routine_id_created_at", "routine_run", ["routine_id", "created_at"])
    if "ix_routine_run_state" not in existing:
        op.create_index("ix_routine_run_state", "routine_run", ["state"])
    if "uq_routine_run_occurrence" not in existing:
        op.create_index(
            "uq_routine_run_occurrence",
            "routine_run",
            ["routine_id", "scheduled_for"],
            unique=True,
            sqlite_where=sa.text("trigger = 'schedule'"),
            postgresql_where=sa.text("trigger = 'schedule'"),
        )

    if offline or "notification" not in _tables():
        op.create_table(
            "notification",
            sa.Column("id", _ID, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("routine_id", sa.BigInteger(), nullable=True),
            sa.Column("run_id", sa.BigInteger(), nullable=True),
            sa.Column("thread_id", sa.BigInteger(), nullable=True),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
    existing = set() if offline else _indexes("notification")
    if "ix_notification_owner_id_read_at" not in existing:
        op.create_index("ix_notification_owner_id_read_at", "notification", ["owner_id", "read_at"])
    if "ix_notification_owner_id_created_at" not in existing:
        op.create_index("ix_notification_owner_id_created_at", "notification", ["owner_id", "created_at"])


def downgrade():
    op.drop_index("ix_notification_owner_id_created_at", table_name="notification")
    op.drop_index("ix_notification_owner_id_read_at", table_name="notification")
    op.drop_table("notification")
    op.drop_index("uq_routine_run_occurrence", table_name="routine_run")
    op.drop_index("ix_routine_run_state", table_name="routine_run")
    op.drop_index("ix_routine_run_routine_id_created_at", table_name="routine_run")
    op.drop_table("routine_run")
    op.drop_index("ix_routine_paused_next_run_at", table_name="routine")
    op.drop_table("routine")
