"""source switches and company display names

Revision ID: b7d2e9a4c1f6
Revises: e8b3d5f1a9c4
Create Date: 2026-10-05 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7d2e9a4c1f6'
down_revision: str | Sequence[str] | None = 'e8b3d5f1a9c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'source_settings',
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('key', sa.String(length=255), nullable=False),
        sa.Column('hidden', sa.Boolean(), server_default='false', nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint('source', 'key'),
    )
    op.create_table(
        'company_names',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('company_names')
    op.drop_table('source_settings')
