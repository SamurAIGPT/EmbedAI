"""An agent can be switched to never use the helper model.

Revision ID: b6d1f4a9c3e7
Revises: a5c3e8b1d7f2
"""
from alembic import context, op
import sqlalchemy as sa

revision = "b6d1f4a9c3e7"
down_revision = "a5c3e8b1d7f2"
branch_labels = None
depends_on = None


def upgrade():
    # Checked first, so a re-run after a crash completes it.
    offline = context.is_offline_mode()
    if offline or "use_helper" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent")}:
        op.add_column(
            "agent",
            sa.Column("use_helper", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade():
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("use_helper")
