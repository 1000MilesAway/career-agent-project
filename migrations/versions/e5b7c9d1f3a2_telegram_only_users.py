"""users who signed up with Telegram have no email or password

Revision ID: e5b7c9d1f3a2
Revises: d2f6a8c1e4b9
Create Date: 2026-09-30 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5b7c9d1f3a2'
down_revision: str | Sequence[str] | None = 'd2f6a8c1e4b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('users', 'email', existing_type=sa.String(length=320), nullable=True)
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=True)
    # Without either, an account would be reachable by nothing at all.
    op.create_check_constraint(
        'has_a_login',
        'users',
        '(email IS NOT NULL AND password_hash IS NOT NULL) OR telegram_id IS NOT NULL',
    )


def downgrade() -> None:
    """Downgrade schema.

    Refuses while Telegram-only accounts exist: making the columns required
    again would need an email nobody gave.
    """
    op.drop_constraint('has_a_login', 'users', type_='check')
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=False)
    op.alter_column('users', 'email', existing_type=sa.String(length=320), nullable=False)
