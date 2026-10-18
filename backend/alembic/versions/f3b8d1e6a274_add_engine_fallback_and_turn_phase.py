"""An agent may name a fallback engine (and model) for when its own engine is
limited; a turn records whether its prompt was handed to the engine (null for
older turns).

Revision ID: f3b8d1e6a274
Revises: e7a91c4d2b58
"""
from alembic import context, op
import sqlalchemy as sa

revision = "f3b8d1e6a274"
down_revision = "e7a91c4d2b58"
branch_labels = None
depends_on = None


def _missing(table: str, column: str) -> bool:
    if context.is_offline_mode():
        return True
    return column not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    # Plain nullable ADD COLUMNs: no table rebuild on SQLite. Each is checked
    # first, so a re-run after a crash completes the migration.
    if _missing("agent", "fallback_provider"):
        op.add_column("agent", sa.Column("fallback_provider", sa.String(), nullable=True))
    if _missing("agent", "fallback_model"):
        op.add_column("agent", sa.Column("fallback_model", sa.String(), nullable=True))
    if _missing("turn", "engine_phase"):
        op.add_column("turn", sa.Column("engine_phase", sa.String(), nullable=True))


def downgrade():
    # Plain DROP COLUMNs (SQLite 3.35+ does them in place): rebuilding `turn`
    # or `agent` would trip the foreign keys that point at them.
    op.drop_column("turn", "engine_phase")
    op.drop_column("agent", "fallback_model")
    op.drop_column("agent", "fallback_provider")
