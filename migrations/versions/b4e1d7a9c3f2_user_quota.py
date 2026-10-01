"""user: plan and daily / weekly quota windows

Revision ID: b4e1d7a9c3f2
Revises: f3c8a1d6e2b5
Create Date: 2026-10-01 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b4e1d7a9c3f2'
down_revision: str | Sequence[str] | None = 'f3c8a1d6e2b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('paid_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('quota_day', sa.Date(), nullable=True))
    op.add_column(
        'users',
        sa.Column('quota_day_usd', sa.Numeric(12, 6), server_default='0', nullable=False),
    )
    op.add_column('users', sa.Column('quota_week', sa.Date(), nullable=True))
    op.add_column(
        'users',
        sa.Column('quota_week_usd', sa.Numeric(12, 6), server_default='0', nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'quota_week_usd')
    op.drop_column('users', 'quota_week')
    op.drop_column('users', 'quota_day_usd')
    op.drop_column('users', 'quota_day')
    op.drop_column('users', 'paid_until')
