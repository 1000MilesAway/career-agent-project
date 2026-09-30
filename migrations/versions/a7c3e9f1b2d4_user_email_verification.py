"""email confirmation for self-signups

Revision ID: a7c3e9f1b2d4
Revises: e5b7c9d1f3a2
Create Date: 2026-09-30 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b2d4'
down_revision: str | Sequence[str] | None = 'e5b7c9d1f3a2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(timezone=True)))
    op.add_column('users', sa.Column('verification_sent_at', sa.DateTime(timezone=True)))
    # Every email account so far was made by the admin CLI, which vouches for
    # the address. Left null, login would lock all of them out.
    op.execute("UPDATE users SET email_verified_at = created_at WHERE email IS NOT NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'verification_sent_at')
    op.drop_column('users', 'email_verified_at')
