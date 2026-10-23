"""Add shell and desktop computer kinds."""
from alembic import context, op
import sqlalchemy as sa
revision = "b9d3e7a1c502"
down_revision = "e7a4c9d2f601"
branch_labels = None
depends_on = None
def upgrade():
    if context.is_offline_mode() or "kind" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("computer")}:
        op.add_column("computer", sa.Column("kind", sa.String(), nullable=False, server_default="shell"))
def downgrade():
    op.drop_column("computer", "kind")
