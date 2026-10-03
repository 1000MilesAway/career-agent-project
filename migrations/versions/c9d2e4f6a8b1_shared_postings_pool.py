"""shared postings pool: listing data moves out of the per-user table

Revision ID: c9d2e4f6a8b1
Revises: b4e1d7a9c3f2
Create Date: 2026-10-03 12:00:00.000000

`vacancies` held every posting once per user who found it — 8 594 rows for
3 690 postings on the live base, 91% of them dropped by the free filters.
The posting moves to `postings`, one row for everyone; `vacancies` keeps
only what is the user's: stage, verdicts, saved/hidden, and what was bought.

Every posting moves to the pool, dropped ones included — they are what the
next user's scan matches. The user's own row for a posting the free filters
dropped is deleted: the reason is now computed from the pool on request
(docs/decisions.md, 2026-10-03). A dropped row the user acted on — saved,
hidden, evaluated, tracked — is kept.

The downgrade restores the columns and copies the listing back; the deleted
screened-out rows do not come back (a rescan recreates them).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c9d2e4f6a8b1'
down_revision: str | Sequence[str] | None = 'b4e1d7a9c3f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Listing columns that leave `vacancies` for `postings`.
LISTING = (
    'source', 'external_id', 'url', 'title', 'company', 'qualification', 'salary_text',
    'location', 'remote', 'accredited', 'published', 'divisions', 'skills', 'raw_text',
    'dedup_key', 'text_fp', 'checked_at', 'closed_at', 'closed_reason',
)


def upgrade() -> None:
    op.create_table(
        'postings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('external_id', sa.String(length=128), nullable=False),
        sa.Column('segment', sa.String(length=32), nullable=True),
        sa.Column('url', sa.String(length=1024), nullable=True),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('company', sa.String(length=255), nullable=True),
        sa.Column('qualification', sa.String(length=32), nullable=True),
        sa.Column('salary_text', sa.String(length=255), nullable=True),
        sa.Column('salary_from', sa.Integer(), nullable=True),
        sa.Column('salary_to', sa.Integer(), nullable=True),
        sa.Column('currency', sa.String(length=8), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('remote', sa.Boolean(), nullable=True),
        sa.Column('accredited', sa.Boolean(), nullable=True),
        sa.Column('published', sa.DateTime(timezone=True), nullable=True),
        sa.Column('divisions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('skills', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('needs_text', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('description_url', sa.String(length=1024), nullable=True),
        sa.Column('duplicate_of_id', sa.UUID(), nullable=True),
        sa.Column('dedup_key', sa.String(length=255), nullable=True),
        sa.Column('text_fp', sa.String(length=16), nullable=True),
        sa.Column(
            'last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_reason', sa.String(length=255), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['duplicate_of_id'], ['postings.id'],
            name=op.f('fk_postings_duplicate_of_id_postings'), ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_postings')),
        sa.UniqueConstraint('source', 'external_id', name='uq_postings_source_ext'),
    )
    op.create_index('ix_postings_source_seen', 'postings', ['source', 'last_seen_at'])
    op.create_index('ix_postings_dedup_key', 'postings', ['dedup_key'])

    op.create_table(
        'source_fetches',
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('key', sa.String(length=512), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('source', 'key', name=op.f('pk_source_fetches')),
    )

    # One posting per (source, external_id). Listing fields from the freshest
    # copy, the liveness verdict from the latest check, the text from the
    # first copy that had one — the text is never overwritten, so the first
    # is the one every later copy would have kept too.
    op.execute("""
        INSERT INTO postings (
            id, source, external_id, url, title, company, qualification, salary_text,
            location, remote, accredited, published, divisions, skills, raw_text,
            dedup_key, text_fp, checked_at, closed_at, closed_reason,
            created_at, updated_at, last_seen_at
        )
        SELECT DISTINCT ON (v.source, v.external_id)
            gen_random_uuid(), v.source, v.external_id, v.url, v.title, v.company,
            v.qualification, v.salary_text, v.location, v.remote, v.accredited,
            v.published, v.divisions, v.skills,
            (SELECT t.raw_text FROM vacancies t
              WHERE t.source = v.source AND t.external_id = v.external_id
                AND t.raw_text IS NOT NULL
              ORDER BY t.created_at LIMIT 1),
            v.dedup_key, v.text_fp,
            c.checked_at, c.closed_at, c.closed_reason,
            (SELECT min(t.created_at) FROM vacancies t
              WHERE t.source = v.source AND t.external_id = v.external_id),
            v.updated_at, v.updated_at
        FROM vacancies v
        LEFT JOIN LATERAL (
            SELECT t.checked_at, t.closed_at, t.closed_reason FROM vacancies t
            WHERE t.source = v.source AND t.external_id = v.external_id
            ORDER BY t.checked_at DESC NULLS LAST LIMIT 1
        ) c ON true
        ORDER BY v.source, v.external_id, v.updated_at DESC
    """)

    op.add_column('vacancies', sa.Column('posting_id', sa.UUID(), nullable=True))
    op.execute("""
        UPDATE vacancies v SET posting_id = p.id
        FROM postings p
        WHERE p.source = v.source AND p.external_id = v.external_id
    """)

    # Per-user duplicate links become pool links, then collapse onto the root.
    op.execute("""
        UPDATE postings p SET duplicate_of_id = po.id
        FROM vacancies v
        JOIN vacancies o ON o.id = v.duplicate_of_id
        JOIN postings po ON po.id = o.posting_id
        WHERE v.posting_id = p.id AND po.id <> p.id AND p.duplicate_of_id IS NULL
    """)
    # Two users can have recorded the same pair both ways round.
    op.execute("""
        UPDATE postings p SET duplicate_of_id = NULL
        FROM postings q
        WHERE p.duplicate_of_id = q.id AND q.duplicate_of_id = p.id AND p.id > q.id
    """)
    op.execute("""
        UPDATE postings p SET duplicate_of_id = q.duplicate_of_id
        FROM postings q
        WHERE p.duplicate_of_id = q.id AND q.duplicate_of_id IS NOT NULL
          AND q.duplicate_of_id <> p.id
    """)

    # Dropped by the free filters and never touched by the user: the posting
    # is pooled above, the user's copy of it goes. Duplicates are among them —
    # dedup is the pool's business now.
    op.execute("""
        DELETE FROM vacancies v
        WHERE v.stage = 'screened_out'
          AND NOT v.is_saved
          AND v.hidden_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM evaluations e WHERE e.vacancy_id = v.id)
          AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.vacancy_id = v.id)
    """)

    op.alter_column('vacancies', 'posting_id', nullable=False)
    op.create_foreign_key(
        op.f('fk_vacancies_posting_id_postings'), 'vacancies', 'postings',
        ['posting_id'], ['id'], ondelete='CASCADE',
    )
    op.create_index(op.f('ix_vacancies_posting_id'), 'vacancies', ['posting_id'])
    op.create_unique_constraint(
        'uq_vacancies_user_posting', 'vacancies', ['user_id', 'posting_id']
    )

    op.drop_constraint('uq_vacancies_user_source_ext', 'vacancies', type_='unique')
    op.drop_index('ix_vacancies_user_dedup_key', table_name='vacancies')
    op.drop_constraint('fk_vacancies_duplicate_of_id', 'vacancies', type_='foreignkey')
    op.drop_column('vacancies', 'duplicate_of_id')
    for column in LISTING:
        op.drop_column('vacancies', column)


def downgrade() -> None:
    op.add_column('vacancies', sa.Column('source', sa.String(length=32), nullable=True))
    op.add_column('vacancies', sa.Column('external_id', sa.String(length=128), nullable=True))
    op.add_column('vacancies', sa.Column('url', sa.String(length=1024), nullable=True))
    op.add_column('vacancies', sa.Column('title', sa.String(length=512), nullable=True))
    op.add_column('vacancies', sa.Column('company', sa.String(length=255), nullable=True))
    op.add_column('vacancies', sa.Column('qualification', sa.String(length=32), nullable=True))
    op.add_column('vacancies', sa.Column('salary_text', sa.String(length=255), nullable=True))
    op.add_column('vacancies', sa.Column('location', sa.String(length=255), nullable=True))
    op.add_column('vacancies', sa.Column('remote', sa.Boolean(), nullable=True))
    op.add_column('vacancies', sa.Column('accredited', sa.Boolean(), nullable=True))
    op.add_column('vacancies', sa.Column('published', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'vacancies',
        sa.Column('divisions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        'vacancies', sa.Column('skills', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column('vacancies', sa.Column('raw_text', sa.Text(), nullable=True))
    op.add_column('vacancies', sa.Column('dedup_key', sa.String(length=255), nullable=True))
    op.add_column('vacancies', sa.Column('text_fp', sa.String(length=16), nullable=True))
    op.add_column(
        'vacancies', sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('vacancies', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('vacancies', sa.Column('closed_reason', sa.String(length=255), nullable=True))
    op.add_column('vacancies', sa.Column('duplicate_of_id', sa.UUID(), nullable=True))

    columns = ', '.join(f'{c} = p.{c}' for c in LISTING)
    op.execute(f"UPDATE vacancies v SET {columns} FROM postings p WHERE p.id = v.posting_id")

    op.alter_column('vacancies', 'source', nullable=False)
    op.alter_column('vacancies', 'external_id', nullable=False)
    op.alter_column('vacancies', 'title', nullable=False)
    op.create_foreign_key(
        'fk_vacancies_duplicate_of_id', 'vacancies', 'vacancies',
        ['duplicate_of_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index('ix_vacancies_user_dedup_key', 'vacancies', ['user_id', 'dedup_key'])
    op.create_unique_constraint(
        'uq_vacancies_user_source_ext', 'vacancies', ['user_id', 'source', 'external_id']
    )

    op.drop_constraint('uq_vacancies_user_posting', 'vacancies', type_='unique')
    op.drop_index(op.f('ix_vacancies_posting_id'), table_name='vacancies')
    op.drop_constraint(op.f('fk_vacancies_posting_id_postings'), 'vacancies', type_='foreignkey')
    op.drop_column('vacancies', 'posting_id')

    op.drop_table('source_fetches')
    op.drop_index('ix_postings_dedup_key', table_name='postings')
    op.drop_index('ix_postings_source_seen', table_name='postings')
    op.drop_table('postings')
