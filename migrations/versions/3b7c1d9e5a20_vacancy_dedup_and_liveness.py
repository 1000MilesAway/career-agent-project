"""vacancy dedup and liveness

Revision ID: 3b7c1d9e5a20
Revises: fa29ef200852
Create Date: 2026-09-23 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3b7c1d9e5a20'
down_revision: str | Sequence[str] | None = 'fa29ef200852'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('vacancies', sa.Column('duplicate_of_id', sa.UUID(), nullable=True))
    op.add_column('vacancies', sa.Column('dedup_key', sa.String(length=255), nullable=True))
    op.add_column('vacancies', sa.Column('text_fp', sa.String(length=16), nullable=True))
    op.add_column(
        'vacancies', sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'vacancies', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('vacancies', sa.Column('closed_reason', sa.String(length=255), nullable=True))
    op.create_foreign_key(
        'fk_vacancies_duplicate_of_id', 'vacancies', 'vacancies',
        ['duplicate_of_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(
        'ix_vacancies_user_dedup_key', 'vacancies', ['user_id', 'dedup_key'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_vacancies_user_dedup_key', table_name='vacancies')
    op.drop_constraint('fk_vacancies_duplicate_of_id', 'vacancies', type_='foreignkey')
    op.drop_column('vacancies', 'closed_reason')
    op.drop_column('vacancies', 'closed_at')
    op.drop_column('vacancies', 'checked_at')
    op.drop_column('vacancies', 'text_fp')
    op.drop_column('vacancies', 'dedup_key')
    op.drop_column('vacancies', 'duplicate_of_id')
