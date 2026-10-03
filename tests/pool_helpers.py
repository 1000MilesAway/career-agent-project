"""Build a user's vacancy row together with the shared posting it points at.

The posting fields live on `postings` since the shared pool
(docs/decisions.md, 2026-10-03); tests that used to write them straight onto
`Vacancy` go through here. A sibling module, like `dbmark`, because `tests/`
is not a package.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.models import Posting, Vacancy

PERSONAL = {"stage", "screen_reason", "triage_verdict", "hidden_at", "is_saved", "saved_at"}


def make_vacancy(user_id: uuid.UUID, **fields: Any) -> Vacancy:
    """A `Vacancy` with its `Posting`; personal fields go to the row, the rest
    to the posting. Adding the row to a session adds the posting too."""
    personal = {k: v for k, v in fields.items() if k in PERSONAL}
    listing = {k: v for k, v in fields.items() if k not in PERSONAL}
    listing.setdefault("source", "habr")
    listing.setdefault("external_id", uuid.uuid4().hex[:8])
    listing.setdefault("title", "Python")
    return Vacancy(user_id=user_id, posting=Posting(id=uuid.uuid4(), **listing), **personal)
