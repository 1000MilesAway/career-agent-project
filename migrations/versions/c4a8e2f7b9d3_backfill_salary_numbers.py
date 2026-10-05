"""salary numbers for postings that carry only the text

Revision ID: c4a8e2f7b9d3
Revises: b7d2e9a4c1f6
Create Date: 2026-10-05 18:00:00.000000

Postings moved into the pool from per-user rows (c9d2e4f6a8b1) brought only
`salary_text`; `/api/state` used to parse it on every call. It now reads the
columns, so the text is parsed once here, by the parser the sources use, and
written the way they write it: the currency always, the numbers only for
roubles (core/tg_scan.py).

A data migration importing `core`: the parser is pure and this runs once.
Rows a source already gave a currency are left alone — that source decided.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from core.sources.salary_text import parse_salary

# revision identifiers, used by Alembic.
revision: str = 'c4a8e2f7b9d3'
down_revision: str | Sequence[str] | None = 'b7d2e9a4c1f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, salary_text FROM postings "
        "WHERE salary_text IS NOT NULL AND currency IS NULL "
        "AND salary_from IS NULL AND salary_to IS NULL"
    )).all()
    for pid, text in rows:
        band = parse_salary(text)
        if band is None:
            continue
        rub = band.currency == "RUB"
        conn.execute(
            sa.text(
                "UPDATE postings SET salary_from = :lo, salary_to = :hi, currency = :cur "
                "WHERE id = :id"
            ),
            {
                "id": pid,
                "lo": (band.min or None) if rub else None,
                "hi": (band.max or None) if rub else None,
                "cur": band.currency,
            },
        )


def downgrade() -> None:
    # Nothing to undo safely: a filled row cannot be told from one a source
    # wrote, and the numbers agree with the text either way.
    pass
