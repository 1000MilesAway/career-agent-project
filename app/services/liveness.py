"""Keep `vacancies.closed_at` honest. Free: HTTP only, no model.

Three places ask:
  - before a paid step (evaluation), so the user does not pay to learn about
    a vacancy that has already been archived — and so the model is not handed
    Habr's "Вакансия в архиве" stub as if it were a job description;
  - on request, over the saved and shortlisted rows, because those are the
    ones the user is about to act on;
  - the worker's background sweep (`sweep`), over every user's live rows, so
    the search list stops showing postings that closed since the scan without
    anyone pressing a button.

Sources with no page to ask (employer boards) are never closed here. Their only signal is age, and age is not evidence of closing — see
`stale_reason`, which marks such a row old without closing it.

A closed vacancy is marked, never deleted or hidden: it keeps its report and
materials, and the list shows it greyed with the reason. "Unknown" changes
nothing but `checked_at` — see core/liveness.py for why unknown is not closed.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import liveness

from ..models import Vacancy
from .scan import EVALUATED, SCREENED_OUT, SHORTLIST, TRIAGED_OUT

# A page checked this recently is not asked again: a posting closed an hour
# ago is rare, and the recheck walks someone else's site.
FRESH_FOR = timedelta(hours=12)
# One recheck is bounded: it runs inside a request, one page per vacancy.
MAX_PER_RECHECK = 30
DELAY_SECONDS = 0.5
# The background sweep's batch. Small enough that one pass is about a minute
# of polite requests; the worker repeats it every few minutes, oldest first,
# so a few hundred rows are all revisited well inside FRESH_FOR.
MAX_PER_SWEEP = 40
# Sources whose rows carry a page `core.liveness` can read. Filtered in SQL
# so rows that can never be checked do not crowd the batch; "url" is a link
# the user pasted, which may point at either board.
CHECKABLE_SOURCES = ("habr", "geekjob", "telegram", "url")
# A posting older than this, from a source that cannot be asked, is shown as
# old. Not closed: nothing says it is, and closing is reserved for evidence.
STALE_AFTER = timedelta(days=30)


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


def _due(now: datetime):
    return or_(Vacancy.checked_at.is_(None), Vacancy.checked_at < now - FRESH_FOR)


async def _check_rows(rows: list[Vacancy], now: datetime) -> dict[str, int]:
    verdicts = await run_in_threadpool(_check_many, [v.url for v in rows])
    counts = {"checked": len(rows), "open": 0, "closed": 0, "unknown": 0}
    for row, verdict in zip(rows, verdicts, strict=True):
        _apply(row, verdict, now)
        counts[verdict.state] += 1
    return counts


async def sweep(session: AsyncSession, limit: int = MAX_PER_SWEEP) -> dict[str, int]:
    """One background pass over every user's rows that are still in play.

    Dropped rows are skipped — they already carry a reason, and checking them
    would spend the batch on what the user does not look at — unless saved.
    Hidden rows are skipped too: the user said they are not interested.
    Closed rows ARE rechecked, because Habr brings archived postings back.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Vacancy)
        .where(
            Vacancy.hidden_at.is_(None),
            Vacancy.source.in_(CHECKABLE_SOURCES),
            or_(
                Vacancy.is_saved.is_(True),
                Vacancy.stage.notin_((SCREENED_OUT, TRIAGED_OUT)),
            ),
            _due(now),
        )
        .order_by(Vacancy.checked_at.asc().nulls_first())
        .limit(limit * 2)
    )
    rows = [v for v in result.scalars() if liveness.can_check(v.url)][:limit]
    if not rows:
        return {"checked": 0, "open": 0, "closed": 0, "unknown": 0}
    return await _check_rows(rows, now)


def stale_reason(vacancy: Vacancy, now: datetime | None = None) -> str | None:
    """Why a row that cannot be asked counts as old, or None.

    Only for sources `core.liveness` cannot read: a checkable row has a real
    answer in `closed_at`, and age must not overrule it.
    """
    if vacancy.published is None or liveness.can_check(vacancy.url):
        return None
    now = now or datetime.now(UTC)
    age = now - vacancy.published
    if age <= STALE_AFTER:
        return None
    return f"опубликована {age.days} дн. назад, проверить у источника нельзя"


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
            or_(
                Vacancy.is_saved.is_(True),
                and_(
                    Vacancy.stage.in_((SHORTLIST, EVALUATED)), Vacancy.hidden_at.is_(None)
                ),
            ),
            _due(now),
        )
        .order_by(Vacancy.checked_at.asc().nulls_first())
    )
    rows = [v for v in result.scalars() if liveness.can_check(v.url)][:MAX_PER_RECHECK]
    return await _check_rows(rows, now)
