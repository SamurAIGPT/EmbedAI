"""Detach retained notifications from conversations that were deleted earlier.

Revision ID: a4c91d7e2f60
Revises: f3b8d1e6a274
Create Date: 2026-10-19
"""
from alembic import op

revision = "a4c91d7e2f60"
down_revision = "f3b8d1e6a274"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Notifications stay in the feed after their conversation is deleted, but
    # an obsolete navigation hint must not send the user to a missing thread.
    op.execute(
        """
        UPDATE notification
        SET thread_id = NULL
        WHERE thread_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM thread WHERE thread.id = notification.thread_id
          )
        """
    )


def downgrade() -> None:
    # The deleted thread ids cannot be recovered.
    pass
