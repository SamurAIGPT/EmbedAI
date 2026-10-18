"""Named agents, and the agent a thread was created from.

Revision ID: 7c4d2e8f1a63
Revises: 6b8e2f0d3c51
"""
from alembic import context, op
import sqlalchemy as sa

revision = "7c4d2e8f1a63"
down_revision = "6b8e2f0d3c51"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite can keep a created table or column after a crash before the
    # version stamp, so each step checks first and re-running completes it.
    offline = context.is_offline_mode()
    inspector = None if offline else sa.inspect(op.get_bind())
    if offline or "agent" not in inspector.get_table_names():
        op.create_table(
            "agent",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
            sa.Column("provider", sa.String(), nullable=False, server_default="claude"),
            sa.Column("model", sa.String(), nullable=True),
            sa.Column("workspace_path", sa.String(), nullable=True),
            sa.Column("approval_mode", sa.String(), nullable=False, server_default="ask"),
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "uq_agent_owner_name_live",
            "agent",
            ["owner_id", "name"],
            unique=True,
            sqlite_where=sa.text("archived_at IS NULL"),
            postgresql_where=sa.text("archived_at IS NULL"),
        )
    if offline or "agent_id" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("thread")}:
        op.add_column("thread", sa.Column("agent_id", sa.BigInteger(), nullable=True))


def downgrade():
    with op.batch_alter_table("thread") as batch:
        batch.drop_column("agent_id")
    op.drop_index("uq_agent_owner_name_live", table_name="agent")
    op.drop_table("agent")
