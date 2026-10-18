"""An agent's notes can be switched off.

Revision ID: c9bc6a5c0df4
Revises: 7c4d2e8f1a63
"""
from alembic import context, op
import sqlalchemy as sa

revision = "c9bc6a5c0df4"
down_revision = "7c4d2e8f1a63"
branch_labels = None
depends_on = None


def upgrade():
    # A plain ADD COLUMN with a constant default works on SQLite without a
    # table rebuild. Checked first, so a re-run after a crash completes it.
    offline = context.is_offline_mode()
    if offline or "memory_enabled" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}:
        op.add_column(
            "agent",
            sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade():
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("memory_enabled")
