"""where an account came from

Revision ID: e3b9c1a7d5f2
Revises: cdad48b73454
Create Date: 2026-10-19 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3b9c1a7d5f2'
down_revision: str | Sequence[str] | None = 'cdad48b73454'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('ref', sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'ref')
