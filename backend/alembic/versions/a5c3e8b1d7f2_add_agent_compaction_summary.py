"""A model-written summary of a folded conversation can be switched off per agent.

Revision ID: a5c3e8b1d7f2
Revises: d6a9f2c1b803
"""
from alembic import context, op
import sqlalchemy as sa

revision = "a5c3e8b1d7f2"
down_revision = "d6a9f2c1b803"
branch_labels = None
depends_on = None


def upgrade():
    # Checked first, so a re-run after a crash completes it.
    offline = context.is_offline_mode()
    if offline or "compaction_summary" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}:
        op.add_column(
            "agent",
            sa.Column("compaction_summary", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade():
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("compaction_summary")
