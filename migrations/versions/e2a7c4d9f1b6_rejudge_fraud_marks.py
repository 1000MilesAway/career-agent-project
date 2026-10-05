"""re-judge postings.fraud_reason under the narrowed payment rules

Revision ID: e2a7c4d9f1b6
Revises: d1f6a3b8c5e2
Create Date: 2026-10-05 21:00:00.000000

The first rules marked two employer postings whose clients, not candidates,
pay ("помогать клиенту внести взнос"). The rules now ask for a payment
demanded of the candidate; marks already stored are recomputed, cleared ones
included. Rules change by data, so this is how a change reaches stored rows.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from core import fraud

# revision identifiers, used by Alembic.
revision: str = 'e2a7c4d9f1b6'
down_revision: str | Sequence[str] | None = 'd1f6a3b8c5e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, title, raw_text, fraud_reason FROM postings")).all()
    for pid, title, text, old in rows:
        new = fraud.check(title, text)
        if new != old:
            conn.execute(
                sa.text("UPDATE postings SET fraud_reason = :r WHERE id = :id"),
                {"r": new, "id": pid},
            )


def downgrade() -> None:
    # Marks are derived data; the previous rules are gone with their file.
    pass
