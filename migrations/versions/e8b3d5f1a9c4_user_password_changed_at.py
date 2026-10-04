"""when the user's password last changed

Revision ID: e8b3d5f1a9c4
Revises: a1e5c3f7b9d2
Create Date: 2026-10-04 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e8b3d5f1a9c4'
down_revision: str | Sequence[str] | None = 'a1e5c3f7b9d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users', sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'password_changed_at')
