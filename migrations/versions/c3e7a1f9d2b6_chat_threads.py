"""several chat threads per user, one per vacancy at most

Revision ID: c3e7a1f9d2b6
Revises: b5d1e9a3c7f2
Create Date: 2026-10-07 21:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3e7a1f9d2b6'
down_revision: str | Sequence[str] | None = 'b5d1e9a3c7f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'chat_threads',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('title', sa.String(120), nullable=False, server_default=''),
        sa.Column(
            'vacancy_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('vacancies.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'last_message_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index('ix_chat_threads_user_last', 'chat_threads', ['user_id', 'last_message_at'])
    op.create_index(
        'uq_chat_threads_user_vacancy',
        'chat_threads',
        ['user_id', 'vacancy_id'],
        unique=True,
        postgresql_where=sa.text('vacancy_id IS NOT NULL'),
    )

    op.add_column(
        'chat_messages', sa.Column('thread_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    # The one conversation each user had becomes their first thread.
    op.execute(
        """
        INSERT INTO chat_threads (id, user_id, title, last_message_at, created_at, updated_at)
        SELECT gen_random_uuid(), user_id, 'Разговор', max(created_at), min(created_at), now()
        FROM chat_messages GROUP BY user_id
        """
    )
    op.execute(
        """
        UPDATE chat_messages m SET thread_id = t.id
        FROM chat_threads t WHERE t.user_id = m.user_id
        """
    )
    op.alter_column('chat_messages', 'thread_id', nullable=False)
    op.create_foreign_key(
        'fk_chat_messages_thread_id',
        'chat_messages',
        'chat_threads',
        ['thread_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.create_index('ix_chat_messages_thread_seq', 'chat_messages', ['thread_id', 'seq'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_chat_messages_thread_seq', table_name='chat_messages')
    op.drop_constraint('fk_chat_messages_thread_id', 'chat_messages', type_='foreignkey')
    op.drop_column('chat_messages', 'thread_id')
    op.drop_index('uq_chat_threads_user_vacancy', table_name='chat_threads')
    op.drop_index('ix_chat_threads_user_last', table_name='chat_threads')
    op.drop_table('chat_threads')
