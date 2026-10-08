"""added vacancies live in «Избранное»: save the ones added before

«Мои вакансии» is gone (docs/decisions.md, 2026-10-08): a vacancy brought by
link is saved on adding. Rows added earlier and never starred would otherwise
show nowhere — the search list leaves added rows out.

Revision ID: d8f3b6a2e1c7
Revises: c3e7a1f9d2b6
Create Date: 2026-10-08 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8f3b6a2e1c7'
down_revision: str | Sequence[str] | None = 'c3e7a1f9d2b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE vacancies SET is_saved = true, saved_at = added_at "
        "WHERE added_at IS NOT NULL AND NOT is_saved"
    )


def downgrade() -> None:
    # Which rows were starred by the user and which by this migration is not
    # recorded; leaving them saved loses nothing.
    pass
