"""resume revisions: parent_id and revision number

Revision ID: 8d4e2f1a6b73
Revises: 3b7c1d9e5a20
Create Date: 2026-09-24 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8d4e2f1a6b73'
down_revision: str | Sequence[str] | None = '3b7c1d9e5a20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('resumes', sa.Column('parent_id', sa.UUID(), nullable=True))
    # server_default backfills every existing row as revision 1.
    op.add_column(
        'resumes',
        sa.Column('revision', sa.Integer(), server_default='1', nullable=False),
    )
    op.create_foreign_key(
        op.f('fk_resumes_parent_id_resumes'),
        'resumes', 'resumes', ['parent_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_resumes_parent_id_resumes'), 'resumes', type_='foreignkey')
    op.drop_column('resumes', 'revision')
    op.drop_column('resumes', 'parent_id')
