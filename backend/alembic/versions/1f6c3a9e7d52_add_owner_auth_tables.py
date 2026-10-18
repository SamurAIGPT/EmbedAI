"""Owner authentication: the owner row, signed-in browsers and paired devices,
and one-time pairing codes (docs/AUTH.md).

New tables only, so the same on SQLite and PostgreSQL. Ids are AUTOINCREMENT on
SQLite so a revoked device's id is never handed to a new one.

Revision ID: 1f6c3a9e7d52
Revises: 4b7e1d9c2a58
"""
from alembic import context, op
import sqlalchemy as sa

revision = "1f6c3a9e7d52"
down_revision = "4b7e1d9c2a58"
branch_labels = None
depends_on = None


def _id_column():
    return sa.Column(
        "id",
        sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
        sa.Identity(always=True),
        primary_key=True,
    )


def upgrade():
    # Checked first, so a re-run after a crash before the version stamp completes it.
    offline = context.is_offline_mode()
    existing = set() if offline else set(sa.inspect(op.get_bind()).get_table_names())

    if "auth_owner" not in existing:
        op.create_table(
            "auth_owner",
            _id_column(),
            sa.Column("password_hash", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sqlite_autoincrement=True,
        )

    if "auth_session" not in existing:
        op.create_table(
            "auth_session",
            _id_column(),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("scopes", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sqlite_autoincrement=True,
        )
    if offline or "uq_auth_session_token_hash" not in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes("auth_session")
    }:
        op.create_index("uq_auth_session_token_hash", "auth_session", ["token_hash"], unique=True)

    if "auth_pairing_code" not in existing:
        op.create_table(
            "auth_pairing_code",
            _id_column(),
            sa.Column("code_hash", sa.String(length=64), nullable=False),
            sa.Column("label", sa.String(), nullable=False, server_default=""),
            sa.Column("scopes", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
            sqlite_autoincrement=True,
        )
    if offline or "uq_auth_pairing_code_code_hash" not in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes("auth_pairing_code")
    }:
        op.create_index("uq_auth_pairing_code_code_hash", "auth_pairing_code", ["code_hash"], unique=True)


def downgrade():
    op.drop_index("uq_auth_pairing_code_code_hash", table_name="auth_pairing_code")
    op.drop_table("auth_pairing_code")
    op.drop_index("uq_auth_session_token_hash", table_name="auth_session")
    op.drop_table("auth_session")
    op.drop_table("auth_owner")
