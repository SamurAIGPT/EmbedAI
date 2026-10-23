"""Quiet memory upkeep (facts noticed in conversations) can be switched off per agent.

Revision ID: c4d8f1a6e903
Revises: b9d3e7a1c502
"""
from alembic import context, op
import sqlalchemy as sa

revision = "c4d8f1a6e903"
down_revision = "b9d3e7a1c502"
branch_labels = None
depends_on = None


def upgrade():
    # Checked first, so a re-run after a crash completes it.
    offline = context.is_offline_mode()
    if offline or "memory_upkeep" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}:
        op.add_column(
            "agent",
            sa.Column("memory_upkeep", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade():
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("memory_upkeep")
