"""Automatic recall before each turn can be switched off per agent.

Revision ID: e7a91c4d2b58
Revises: b5e3c8a1d702
"""
from alembic import context, op
import sqlalchemy as sa

revision = "e7a91c4d2b58"
down_revision = "b5e3c8a1d702"
branch_labels = None
depends_on = None


def upgrade():
    # A plain ADD COLUMN with a constant default works on SQLite without a
    # table rebuild. Checked first, so a re-run after a crash completes it.
    offline = context.is_offline_mode()
    if offline or "recall_enabled" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}:
        op.add_column(
            "agent",
            sa.Column("recall_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade():
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("recall_enabled")
