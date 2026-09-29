"""telegram account linked to a user

Revision ID: b91d3e7a4c52
Revises: c4a8e2d1f907
Create Date: 2026-09-29 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b91d3e7a4c52'
down_revision: str | Sequence[str] | None = 'c4a8e2d1f907'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('telegram_id', sa.BigInteger(), nullable=True))
    op.add_column('users', sa.Column('telegram_name', sa.String(length=128), nullable=True))
    op.create_unique_constraint('uq_users_telegram_id', 'users', ['telegram_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_users_telegram_id', 'users', type_='unique')
    op.drop_column('users', 'telegram_name')
    op.drop_column('users', 'telegram_id')
