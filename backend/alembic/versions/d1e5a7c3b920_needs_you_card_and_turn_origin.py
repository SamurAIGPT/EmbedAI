"""The Needs you card's state (time zone, presence, today's card) and who started a turn.

Revision ID: d1e5a7c3b920
Revises: c4d8f1a6e903
"""
from alembic import context, op
import sqlalchemy as sa

revision = "d1e5a7c3b920"
down_revision = "c4d8f1a6e903"
branch_labels = None
depends_on = None


def upgrade():
    offline = context.is_offline_mode()
    inspector = None if offline else sa.inspect(op.get_bind())
    if offline or "needs_you_state" not in inspector.get_table_names():
        op.create_table(
            "needs_you_state",
            sa.Column("owner_id", sa.String(), primary_key=True),
            sa.Column("time_zone", sa.String(), nullable=True),
            sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("card_day", sa.String(), nullable=True),
            sa.Column("card_thread_id", sa.BigInteger(), nullable=True),
            sa.Column("card_hash", sa.String(), nullable=True),
            sa.Column("card_revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("notified", sa.JSON(), nullable=False),
        )
    if offline or "origin" not in {c["name"] for c in inspector.get_columns("turn")}:
        op.add_column("turn", sa.Column("origin", sa.String(), nullable=True))


def downgrade():
    # A plain DROP COLUMN: rebuilding `turn` would trip the foreign keys that point at it.
    op.drop_column("turn", "origin")
    op.drop_table("needs_you_state")
