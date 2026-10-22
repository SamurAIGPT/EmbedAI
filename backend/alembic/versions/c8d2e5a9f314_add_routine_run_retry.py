"""Automatic retry of failed routine runs: per-routine limit and delay, and
each run's attempt number and the run it retries.

Plain ADD COLUMN with constant defaults and a new index, so SQLite needs no
table rebuild.

Revision ID: c8d2e5a9f314
Revises: a4c91d7e2f60
"""
from alembic import context, op
import sqlalchemy as sa

revision = "c8d2e5a9f314"
down_revision = "a4c91d7e2f60"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    # Each step checks first, so a re-run after a crash before the version
    # stamp completes the upgrade.
    offline = context.is_offline_mode()
    routine = set() if offline else _columns("routine")
    if "retry_limit" not in routine:
        op.add_column("routine", sa.Column("retry_limit", sa.Integer(), nullable=False, server_default="0"))
    if "retry_delay_minutes" not in routine:
        op.add_column(
            "routine", sa.Column("retry_delay_minutes", sa.Integer(), nullable=False, server_default="5")
        )
    run = set() if offline else _columns("routine_run")
    if "attempt" not in run:
        op.add_column("routine_run", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    if "retry_of" not in run:
        op.add_column("routine_run", sa.Column("retry_of", sa.BigInteger(), nullable=True))
    if offline or "uq_routine_run_retry_of" not in _indexes("routine_run"):
        op.create_index("uq_routine_run_retry_of", "routine_run", ["retry_of"], unique=True)


def downgrade():
    # Plain DROP COLUMNs (SQLite 3.35+ does them in place): rebuilding
    # `routine` would trip the foreign key from routine_run.
    op.drop_index("uq_routine_run_retry_of", table_name="routine_run")
    op.drop_column("routine_run", "retry_of")
    op.drop_column("routine_run", "attempt")
    op.drop_column("routine", "retry_delay_minutes")
    op.drop_column("routine", "retry_limit")
