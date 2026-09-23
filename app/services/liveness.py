"""Keep `vacancies.closed_at` honest. Free: HTTP only, no model.

Two places ask:
  - before a paid step (evaluation), so the user does not pay to learn about
    a vacancy that has already been archived — and so the model is not handed
    Habr's "Вакансия в архиве" stub as if it were a job description;
  - on request, over the saved and shortlisted rows, because those are the
    ones the user is about to act on.

A closed vacancy is marked, never deleted or hidden: it keeps its report and
materials, and the list shows it greyed with the reason. "Unknown" changes
nothing but `checked_at` — see core/liveness.py for why unknown is not closed.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import liveness

from ..models import Vacancy
from .scan import EVALUATED, SHORTLIST

# A page checked this recently is not asked again: a posting closed an hour
# ago is rare, and the recheck walks someone else's site.
FRESH_FOR = timedelta(hours=12)
# One recheck is bounded: it runs inside a request, one page per vacancy.
MAX_PER_RECHECK = 30
DELAY_SECONDS = 0.5


def _apply(vacancy: Vacancy, verdict: liveness.Liveness, now: datetime) -> None:
    vacancy.checked_at = now
    if verdict.closed:
        if vacancy.closed_at is None:
            vacancy.closed_at = now
        vacancy.closed_reason = verdict.reason[:255]
    elif verdict.state == "open":
        # Reopened postings exist (Habr brings archived ones back). Only an
        # explicit "open" clears the mark; "unknown" leaves it as it was.
        vacancy.closed_at = None
        vacancy.closed_reason = None


def needs_check(vacancy: Vacancy, now: datetime | None = None) -> bool:
    if not liveness.can_check(vacancy.url):
        return False
    now = now or datetime.now(UTC)
    return vacancy.checked_at is None or now - vacancy.checked_at > FRESH_FOR


async def refresh(vacancy: Vacancy) -> liveness.Liveness:
    """Check one vacancy now and record the verdict on the row."""
    verdict = await run_in_threadpool(liveness.check, vacancy.url)
    _apply(vacancy, verdict, datetime.now(UTC))
    return verdict


def _check_many(urls: list[str]) -> list[liveness.Liveness]:
    out: list[liveness.Liveness] = []
    with liveness.client() as http:
        for i, url in enumerate(urls):
            if i:
                time.sleep(DELAY_SECONDS)
            out.append(liveness.check(url, http=http))
    return out


async def recheck(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """Check the rows the user is about to act on: saved, or still in play.

    Oldest check first, so repeated presses walk the whole list instead of
    re-asking about the same thirty.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Vacancy)
        .where(
            Vacancy.user_id == user_id,
            or_(Vacancy.is_saved.is_(True), Vacancy.stage.in_((SHORTLIST, EVALUATED))),
            or_(Vacancy.checked_at.is_(None), Vacancy.checked_at < now - FRESH_FOR),
        )
        .order_by(Vacancy.checked_at.asc().nulls_first())
    )
    rows = [v for v in result.scalars() if liveness.can_check(v.url)][:MAX_PER_RECHECK]
    verdicts = await run_in_threadpool(_check_many, [v.url for v in rows])

    counts = {"checked": len(rows), "open": 0, "closed": 0, "unknown": 0}
    for row, verdict in zip(rows, verdicts, strict=True):
        _apply(row, verdict, now)
        counts[verdict.state] += 1
    return counts
