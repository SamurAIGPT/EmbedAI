"""Durable personal task boards and deadlines, separate from execution tasks."""
from alembic import op
import sqlalchemy as sa

revision = "9e6d3b1a7c52"
down_revision = "32f1a61f1173"
branch_labels = None
depends_on = None


def pk():
    return sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(always=True), primary_key=True)


def upgrade():
    op.create_table("work_board", pk(),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.UniqueConstraint("owner_id", "key"), sqlite_autoincrement=True)
    op.create_table("work_column", pk(),
        sa.Column("board_id", sa.BigInteger(), sa.ForeignKey("work_board.id"), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("board_id", "key"), sqlite_autoincrement=True)
    op.create_table("work_item", pk(),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("board_id", sa.BigInteger(), sa.ForeignKey("work_board.id"), nullable=False),
        sa.Column("column_id", sa.BigInteger(), sa.ForeignKey("work_column.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.String(), nullable=False, server_default="normal"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
        sa.Column("agent_id", sa.BigInteger(), nullable=True),
        sa.Column("team_id", sa.BigInteger(), nullable=True),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sqlite_autoincrement=True)
    op.create_index("ix_work_item_owner_board", "work_item", ["owner_id", "board_id"])


def downgrade():
    op.drop_table("work_item")
    op.drop_table("work_column")
    op.drop_table("work_board")
