"""vacancy hidden by the user

Revision ID: c4a8e2d1f907
Revises: 8d4e2f1a6b73
Create Date: 2026-09-27 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4a8e2d1f907'
down_revision: str | Sequence[str] | None = '8d4e2f1a6b73'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'vacancies', sa.Column('hidden_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('vacancies', 'hidden_at')
