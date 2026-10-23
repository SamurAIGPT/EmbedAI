"""Team setup requests: the receipt and undo record for lead-driven team changes.
Revision ID: f5a2c8e1d704
Revises: d1e5a7c3b920
"""
from alembic import context, op
import sqlalchemy as sa
from app.db.types import BigIntPK, UTCDateTime, JSONValue
revision = "f5a2c8e1d704"
down_revision = "d1e5a7c3b920"
branch_labels = None
depends_on = None


def upgrade():
    offline = context.is_offline_mode()
    if offline or not sa.inspect(op.get_bind()).has_table("team_setup_request"):
        op.create_table("team_setup_request",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("team_id", sa.BigInteger(), nullable=False),
            sa.Column("lead_agent_id", sa.BigInteger(), nullable=False),
            sa.Column("thread_id", sa.BigInteger(), nullable=True),
            sa.Column("turn_id", sa.BigInteger(), nullable=True),
            sa.Column("request_id", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False, server_default="setup"),
            sa.Column("plan", JSONValue, nullable=False),
            sa.Column("state", sa.String(), nullable=False, server_default="applied"),
            sa.Column("result", JSONValue, nullable=False),
            sa.Column("undo", JSONValue, nullable=False),
            sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("decided_at", UTCDateTime(), nullable=True),
            sqlite_autoincrement=True,
        )
    if offline or "uq_team_setup_request_owner_request" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("team_setup_request")}:
        op.create_index("uq_team_setup_request_owner_request", "team_setup_request", ["owner_id", "request_id"], unique=True)


def downgrade():
    op.drop_index("uq_team_setup_request_owner_request", table_name="team_setup_request")
    op.drop_table("team_setup_request")
