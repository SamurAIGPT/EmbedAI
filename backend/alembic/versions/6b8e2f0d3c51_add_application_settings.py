"""Application settings and atomic security audit (D-013)."""
from alembic import context, op
import sqlalchemy as sa

revision = "6b8e2f0d3c51"
down_revision = "5a7d1e9c2b40"
branch_labels = None
depends_on = None


def upgrade():
    existing = set() if context.is_offline_mode() else set(sa.inspect(op.get_bind()).get_table_names())
    if "app_setting" not in existing:
        op.create_table("app_setting",
            sa.Column("key", sa.String(), primary_key=True),
            sa.Column("value_json", sa.JSON(none_as_null=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_by", sa.String(), nullable=False))
    if "setting_audit" not in existing:
        op.create_table("setting_audit",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(always=True), primary_key=True),
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("old_value_json", sa.JSON(none_as_null=True), nullable=True),
            sa.Column("new_value_json", sa.JSON(none_as_null=True), nullable=True),
            sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("actor", sa.String(), nullable=False))


def downgrade():
    op.drop_table("setting_audit")
    op.drop_table("app_setting")
