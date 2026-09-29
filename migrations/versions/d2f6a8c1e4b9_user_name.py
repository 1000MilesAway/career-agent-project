"""display name on the user

Revision ID: d2f6a8c1e4b9
Revises: b91d3e7a4c52
Create Date: 2026-09-29 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd2f6a8c1e4b9'
down_revision: str | Sequence[str] | None = 'b91d3e7a4c52'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('name', sa.String(length=80), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'name')
