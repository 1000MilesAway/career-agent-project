"""telegram: the channel or chat is not the company

Revision ID: b3e7d1f9c2a5
Revises: a6d2f8c4e9b1
Create Date: 2026-10-16 14:00:00.000000

Telegram postings carried "@channel" as `company`. The channel is where a
post was read (it stays in `external_id`, "channel/101"); the company is now
the employer the post names, or empty. The next read of a post fills it in.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3e7d1f9c2a5'
down_revision: str | Sequence[str] | None = 'a6d2f8c4e9b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE postings SET company = NULL "
        "WHERE source IN ('telegram', 'telethon') AND company LIKE '@%'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE postings SET company = '@' || split_part(external_id, '/', 1) "
        "WHERE source IN ('telegram', 'telethon') AND company IS NULL"
    )
