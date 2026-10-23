"""Opt-in structured browser tools for each desktop computer."""
from alembic import context, op
import sqlalchemy as sa

revision = "d6a9f2c1b803"
down_revision = "c4d8f1a6e903"
branch_labels = None
depends_on = None


def upgrade():
    if context.is_offline_mode() or "browser_enabled" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("computer")}:
        op.add_column("computer", sa.Column("browser_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("computer") as batch:
        batch.drop_column("browser_enabled")
