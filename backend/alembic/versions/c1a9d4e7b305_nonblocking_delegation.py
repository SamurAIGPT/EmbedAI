"""Non-blocking delegation: root rows, reply modes and the result inbox.
Revision ID: c1a9d4e7b305
Revises: f5a2c8e1d704
"""
from alembic import context, op
import sqlalchemy as sa
from app.db.types import BigIntPK, UTCDateTime
revision = "c1a9d4e7b305"
down_revision = "f5a2c8e1d704"
branch_labels = None
depends_on = None

JOB_COLUMNS = [
    sa.Column("kind", sa.String(), nullable=False, server_default="job"),
    sa.Column("reply", sa.String(), nullable=False, server_default="wait"),
    sa.Column("question", sa.Text(), nullable=True),
    sa.Column("answer", sa.Text(), nullable=True),
    sa.Column("restart", sa.String(), nullable=True),
    sa.Column("restarts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("current_turn_id", sa.BigInteger(), nullable=True),
    sa.Column("rework", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("budget_wakes", sa.Integer(), nullable=False, server_default="48"),
]


def upgrade():
    offline = context.is_offline_mode()
    present = set() if offline else {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent_job")}
    for column in JOB_COLUMNS:
        if column.name not in present:
            op.add_column("agent_job", column)
    if offline or "ix_agent_job_parent" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("agent_job")}:
        op.create_index("ix_agent_job_parent", "agent_job", ["parent_id"], unique=False)
    if offline or not sa.inspect(op.get_bind()).has_table("agent_job_inbox"):
        op.create_table("agent_job_inbox",
            sa.Column("id", BigIntPK, sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("holder_id", sa.BigInteger(), nullable=False),
            sa.Column("job_id", sa.BigInteger(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("created_at", UTCDateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("delivered_at", UTCDateTime(), nullable=True),
            sqlite_autoincrement=True,
        )
    if offline or "ix_agent_job_inbox_holder" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("agent_job_inbox")}:
        op.create_index("ix_agent_job_inbox_holder", "agent_job_inbox", ["holder_id", "delivered_at"], unique=False)
    if offline or "ix_agent_job_inbox_job" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("agent_job_inbox")}:
        op.create_index("ix_agent_job_inbox_job", "agent_job_inbox", ["job_id"], unique=False)


def downgrade():
    op.drop_index("ix_agent_job_inbox_job", table_name="agent_job_inbox")
    op.drop_index("ix_agent_job_inbox_holder", table_name="agent_job_inbox")
    op.drop_table("agent_job_inbox")
    op.drop_index("ix_agent_job_parent", table_name="agent_job")
    with op.batch_alter_table("agent_job") as batch:
        for column in JOB_COLUMNS:
            batch.drop_column(column.name)
