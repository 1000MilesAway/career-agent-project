"""postings: count full-feed reads a posting was missing from

Revision ID: d4a7b2c9e1f3
Revises: c9d2e4f6a8b1
Create Date: 2026-10-03 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'd4a7b2c9e1f3'
down_revision: str | Sequence[str] | None = 'c9d2e4f6a8b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'postings',
        sa.Column('missed_reads', sa.Integer(), server_default='0', nullable=False),
    )
    op.add_column(
        'postings', sa.Column('missing_since', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('postings', 'missing_since')
    op.drop_column('postings', 'missed_reads')
