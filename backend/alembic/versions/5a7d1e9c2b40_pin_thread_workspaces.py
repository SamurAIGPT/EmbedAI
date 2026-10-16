"""Pin the canonical workspace of every existing conversation.

Revision ID: 5a7d1e9c2b40
Revises: 4e8a1c7d9b30
"""
from alembic import context, op
import sqlalchemy as sa

from app.core.config import settings
from app.services.workspaces import canonical_workspace

revision = "5a7d1e9c2b40"
down_revision = "4e8a1c7d9b30"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite can keep an ADD COLUMN after a crash before the version stamp.
    # Re-running completes the backfill without changing already pinned rows.
    if context.is_offline_mode() or "workspace_path" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("thread")
    }:
        op.add_column("thread", sa.Column("workspace_path", sa.String(), nullable=True))
    threads = sa.table("thread", sa.column("workspace_path", sa.String()))
    op.execute(threads.update().where(threads.c.workspace_path.is_(None)).values(
        workspace_path=str(canonical_workspace(settings.LOCAL_WORKSPACE_ROOT))
    ))


def downgrade():
    with op.batch_alter_table("thread") as batch:
        batch.drop_column("workspace_path")
