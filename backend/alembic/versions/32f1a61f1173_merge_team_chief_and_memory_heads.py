"""merge team chief and memory heads

Revision ID: 32f1a61f1173
Revises: a7c3e9d1f206, b6d1f4a9c3e7
Create Date: 2026-10-24 01:20:43.583736

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '32f1a61f1173'
down_revision: Union[str, Sequence[str], None] = ('a7c3e9d1f206', 'b6d1f4a9c3e7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
