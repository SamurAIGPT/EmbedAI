"""Reports to the lead and the ledger that limits retries.
Revision ID: a7c3e9d1f206
Revises: c1a9d4e7b305
"""
from alembic import context, op
import sqlalchemy as sa
from app.db.types import BigIntPK, UTCDateTime
revision = "a7c3e9d1f206"
down_revision = "c1a9d4e7b305"
branch_labels = None
depends_on = None


def upgrade():
    offline = context.is_offline_mode()
    if not offline and sa.inspect(op.get_bind()).has_table("team_notice"):
        return
    op.create_table("team_notice",
        sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("family", sa.String(), nullable=False, server_default="incident"),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("subject_agent_id", sa.BigInteger(), nullable=False),
        sa.Column("subject_thread_id", sa.BigInteger(), nullable=False),
        sa.Column("room_id", sa.BigInteger(), nullable=True),
        sa.Column("key", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("last_request", sa.Text(), nullable=True),
        sa.Column("last_reply", sa.Text(), nullable=True),
        sa.Column("replay", sa.String(), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("already_notified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("chipped", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("lead_turn_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", UTCDateTime(), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_team_notice_thread", "team_notice", ["owner_id", "subject_thread_id", "family", "created_at"], unique=False)
    op.create_index("ix_team_notice_status", "team_notice", ["owner_id", "status"], unique=False)


def downgrade():
    op.drop_index("ix_team_notice_status", table_name="team_notice")
    op.drop_index("ix_team_notice_thread", table_name="team_notice")
    op.drop_table("team_notice")
