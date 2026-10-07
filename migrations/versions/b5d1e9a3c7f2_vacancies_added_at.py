"""vacancies the user brought: a link or a pasted text

Revision ID: b5d1e9a3c7f2
Revises: f7a2c5e8b1d4
Create Date: 2026-10-07 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b5d1e9a3c7f2'
down_revision: str | Sequence[str] | None = 'f7a2c5e8b1d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('vacancies', sa.Column('added_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('vacancies', 'added_at')
