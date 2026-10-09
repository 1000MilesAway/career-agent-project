"""payments: Pro bought through Platega

Revision ID: a6d2f8c4e9b1
Revises: d8f3b6a2e1c7
Create Date: 2026-10-09 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a6d2f8c4e9b1'
down_revision: str | Sequence[str] | None = 'd8f3b6a2e1c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'payments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('transaction_id', sa.String(length=64), nullable=True),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('amount_rub', sa.Integer(), nullable=False),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('transaction_id'),
    )
    op.create_index('ix_payments_user_id', 'payments', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_payments_user_id', table_name='payments')
    op.drop_table('payments')
