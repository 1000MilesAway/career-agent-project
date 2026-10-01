"""vacancy: employer's IT accreditation as the source states it

Revision ID: f3c8a1d6e2b5
Revises: a7c3e9f1b2d4
Create Date: 2026-10-01 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3c8a1d6e2b5'
down_revision: str | Sequence[str] | None = 'a7c3e9f1b2d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('vacancies', sa.Column('accredited', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('vacancies', 'accredited')
