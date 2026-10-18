"""Agent computers: an isolated machine per agent.

A new table only, so it is the same on SQLite and PostgreSQL. On SQLite the id
is AUTOINCREMENT from the start (a container name is derived from it and must
never be handed to a different computer).

Revision ID: 4b7e1d9c2a58
Revises: 8d5f3a2b6c74
"""
from alembic import context, op
import sqlalchemy as sa

revision = "4b7e1d9c2a58"
down_revision = "8d5f3a2b6c74"
branch_labels = None
depends_on = None


def upgrade():
    # Checked first, so a re-run after a crash before the version stamp completes it.
    offline = context.is_offline_mode()
    if offline or "computer" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "computer",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(always=True), primary_key=True),
            sa.Column("owner_id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.BigInteger(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False, server_default="docker"),
            sa.Column("image", sa.String(), nullable=False),
            sa.Column("image_id", sa.String(), nullable=True),
            sa.Column("cpu_millis", sa.Integer(), nullable=False, server_default="1000"),
            sa.Column("memory_mb", sa.Integer(), nullable=False, server_default="1024"),
            sa.Column("pids_limit", sa.Integer(), nullable=False, server_default="256"),
            sa.Column("network", sa.String(), nullable=False, server_default="none"),
            sa.Column("idle_minutes", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("auto_start", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("state", sa.String(), nullable=False, server_default="missing"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("workspace_path", sa.String(), nullable=True),
            sa.Column("container_id", sa.String(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )
    if offline or "uq_computer_agent_id" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("computer")}:
        op.create_index("uq_computer_agent_id", "computer", ["agent_id"], unique=True)


def downgrade():
    op.drop_index("uq_computer_agent_id", table_name="computer")
    op.drop_table("computer")
