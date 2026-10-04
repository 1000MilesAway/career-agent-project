"""llm_calls: a journal of every model call, for the owner's dashboard

Revision ID: a1e5c3f7b9d2
Revises: d4a7b2c9e1f3
Create Date: 2026-10-04 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a1e5c3f7b9d2'
down_revision: str | Sequence[str] | None = 'd4a7b2c9e1f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'llm_calls',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('step', sa.String(length=32), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('cache_hit', sa.Integer(), nullable=False),
        sa.Column('cache_miss', sa.Integer(), nullable=False),
        sa.Column('output', sa.Integer(), nullable=False),
        sa.Column('reasoning', sa.Integer(), nullable=False),
        sa.Column('seconds', sa.Float(), nullable=False),
        sa.Column('usd', sa.Numeric(precision=14, scale=8), nullable=False),
        sa.Column('offpeak_usd', sa.Numeric(precision=14, scale=8), nullable=False),
        sa.Column('rub', sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name=op.f('fk_llm_calls_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_calls')),
    )
    op.create_index('ix_llm_calls_created_at', 'llm_calls', ['created_at'])
    op.create_index('ix_llm_calls_user_created', 'llm_calls', ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_llm_calls_user_created', table_name='llm_calls')
    op.drop_index('ix_llm_calls_created_at', table_name='llm_calls')
    op.drop_table('llm_calls')
