"""the user's portrait for the resume header

Revision ID: f7a2c5e8b1d4
Revises: e2a7c4d9f1b6
Create Date: 2026-10-07 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f7a2c5e8b1d4'
down_revision: str | Sequence[str] | None = 'e2a7c4d9f1b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'user_photos',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('media_type', sa.String(length=32), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('user_photos')
