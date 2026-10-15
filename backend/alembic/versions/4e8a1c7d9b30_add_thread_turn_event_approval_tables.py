"""add thread, turn, event and approval tables

Durable conversations (F04). Additive only: no existing table is touched.
Built from constructs both SQLite and PostgreSQL accept; SQLite is the primary
target. No CHECK constraints (changing one on SQLite means rebuilding the table).

Revision ID: 4e8a1c7d9b30
Revises: d2fc641cb3d1
Create Date: 2026-10-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# SQLite only auto-assigns an id for a column declared INTEGER PRIMARY KEY, so
# identity keys are INTEGER there and BIGINT identity on PostgreSQL. Spelled out
# here (not imported from app.db.types) so this migration never changes.
BIG_ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


# revision identifiers, used by Alembic.
revision: str = '4e8a1c7d9b30'
down_revision: Union[str, Sequence[str], None] = 'd2fc641cb3d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('thread',
    sa.Column('id', BIG_ID, sa.Identity(always=True), nullable=False),
    sa.Column('owner_id', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('provider_session_id', sa.String(), nullable=True),
    sa.Column('session_state', sa.String(), server_default='new', nullable=False),
    sa.Column('provider_state', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('last_seq', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_thread_owner_listing', 'thread', ['owner_id', 'archived_at', 'updated_at'], unique=False)
    op.create_index('uq_thread_provider_session', 'thread', ['provider', 'provider_session_id'], unique=True,
                    sqlite_where=sa.text('provider_session_id IS NOT NULL'),
                    postgresql_where=sa.text('provider_session_id IS NOT NULL'))

    op.create_table('turn',
    sa.Column('id', BIG_ID, sa.Identity(always=True), nullable=False),
    sa.Column('thread_id', sa.BigInteger(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('prompt_text', sa.Text(), nullable=False),
    sa.Column('status', sa.String(), server_default='running', nullable=False),
    sa.Column('error_code', sa.String(), nullable=True),
    sa.Column('result', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('usage', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('owner_token', sa.String(), nullable=True),
    sa.Column('owner_instance', sa.String(), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['thread_id'], ['thread.id'], name='fk_turn_thread_id_thread'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('thread_id', 'seq', name='uq_turn_thread_id_seq')
    )
    op.create_index('uq_turn_one_running_per_thread', 'turn', ['thread_id'], unique=True,
                    sqlite_where=sa.text("status = 'running'"),
                    postgresql_where=sa.text("status = 'running'"))
    op.create_index('ix_turn_status_heartbeat', 'turn', ['status', 'heartbeat_at'], unique=False)

    op.create_table('event',
    sa.Column('id', BIG_ID, sa.Identity(always=True), nullable=False),
    sa.Column('thread_id', sa.BigInteger(), nullable=False),
    sa.Column('turn_id', sa.BigInteger(), nullable=True),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('data', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['thread_id'], ['thread.id'], name='fk_event_thread_id_thread'),
    sa.ForeignKeyConstraint(['turn_id'], ['turn.id'], name='fk_event_turn_id_turn'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('thread_id', 'seq', name='uq_event_thread_id_seq')
    )
    op.create_index('ix_event_turn_id', 'event', ['turn_id'], unique=False)

    op.create_table('approval',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('thread_id', sa.BigInteger(), nullable=False),
    sa.Column('turn_id', sa.BigInteger(), nullable=False),
    sa.Column('request_id', sa.String(), nullable=False),
    sa.Column('tool', sa.String(), nullable=False),
    sa.Column('input', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('kind', sa.String(), nullable=True),
    sa.Column('status', sa.String(), server_default='pending', nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['thread_id'], ['thread.id'], name='fk_approval_thread_id_thread'),
    sa.ForeignKeyConstraint(['turn_id'], ['turn.id'], name='fk_approval_turn_id_turn'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_approval_turn_id_status', 'approval', ['turn_id', 'status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_approval_turn_id_status', table_name='approval')
    op.drop_table('approval')
    op.drop_index('ix_event_turn_id', table_name='event')
    op.drop_table('event')
    op.drop_index('ix_turn_status_heartbeat', table_name='turn')
    op.drop_index('uq_turn_one_running_per_thread', table_name='turn')
    op.drop_table('turn')
    op.drop_index('uq_thread_provider_session', table_name='thread')
    op.drop_index('ix_thread_owner_listing', table_name='thread')
    op.drop_table('thread')
