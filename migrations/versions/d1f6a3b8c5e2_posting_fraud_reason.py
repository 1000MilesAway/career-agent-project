"""postings.fraud_reason: a free fraud mark on the posting's text

Revision ID: d1f6a3b8c5e2
Revises: c4a8e2f7b9d3
Create Date: 2026-10-05 19:00:00.000000

The column is filled whenever the text is written (app/services/pool.py);
postings already pooled are judged here once, by the same core/fraud.py.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from core import fraud

# revision identifiers, used by Alembic.
revision: str = 'd1f6a3b8c5e2'
down_revision: str | Sequence[str] | None = 'c4a8e2f7b9d3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('postings', sa.Column('fraud_reason', sa.String(length=256), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, title, raw_text FROM postings")).all()
    for pid, title, text in rows:
        reason = fraud.check(title, text)
        if reason:
            conn.execute(
                sa.text("UPDATE postings SET fraud_reason = :r WHERE id = :id"),
                {"r": reason, "id": pid},
            )


def downgrade() -> None:
    op.drop_column('postings', 'fraud_reason')
