"""Keep `postings.closed_at` honest. Free: HTTP only, no model.

Three places ask:
  - before a paid step (evaluation), so the user does not pay to learn about
    a vacancy that has already been archived — and so the model is not handed
    Habr's "Вакансия в архиве" stub as if it were a job description;
  - on request, over the saved and shortlisted rows, because those are the
    ones the user is about to act on;
  - the worker's background sweep (`sweep`), over every posting some user
    still has in play, so the search list stops showing postings that closed
    since the scan without anyone pressing a button.

Liveness is a fact about the posting, so it is checked and stored once in the
shared pool: a posting five users have is one request, not five.

Sources with no page to ask (employer feeds) are never closed here. A
feed read whole closes its postings by their absence from it
(`pool.mark_absent`); the searchable boards (Sber, Yandex, VK) are asked
through `core.liveness` like Habr and geekjob, and a checkable row has
only age where nothing can be asked — age is not evidence of closing —
see `stale_reason`, which marks such a row old without closing it.

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
from core.sources import employers

from ..models import Posting, Vacancy
from . import pool
from . import sources as switches_db
from .pool import Sighting, parse_published
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
# so rows that can never be checked do not crowd the batch. Not "url": those
# are pages on any site a user once pasted, and revisiting them would make the
# sweep fetch whatever address the site now redirects to. "company" is safe
# the same way: only the searchable boards' own URLs pass `core.liveness`
# (rabota.sber.ru, yandex.ru/jobs, team.vk.company), while feed postings —
# mts, huntflow, greenhouse, lever, ashby — live on other hosts and keep closing
# by their absence from the feed (`sweep_feeds`), not by their page.
CHECKABLE_SOURCES = ("habr", "geekjob", "telegram", "company")
# A posting older than this, from a source that cannot be asked, is shown as
# old. Not closed: nothing says it is, and closing is reserved for evidence.
STALE_AFTER = timedelta(days=30)
# An employer feed posting no read has returned for this long counts as old:
# nobody scanned that feed since, so its absence was never checked either.
UNSEEN_AFTER = timedelta(days=7)


def _apply(posting: Posting, verdict: liveness.Liveness, now: datetime) -> None:
    posting.checked_at = now
    if posting.published is None and verdict.published:
        # Telegram postings were pooled without their date before 2026-10-05;
        # the widget page carries it, and the age mark needs it.
        posting.published = parse_published(verdict.published)
    if verdict.closed:
        if posting.closed_at is None:
            posting.closed_at = now
        posting.closed_reason = verdict.reason[:255]
    elif verdict.state == "open":
        # Reopened postings exist (Habr brings archived ones back). Only an
        # explicit "open" clears the mark; "unknown" leaves it as it was.
        posting.closed_at = None
        posting.closed_reason = None


def needs_check(vacancy: Vacancy, now: datetime | None = None) -> bool:
    if not liveness.can_check(vacancy.url):
        return False
    now = now or datetime.now(UTC)
    return vacancy.checked_at is None or now - vacancy.checked_at > FRESH_FOR


async def refresh(vacancy: Vacancy) -> liveness.Liveness:
    """Check one vacancy now and record the verdict on the row."""
    verdict = await run_in_threadpool(liveness.check, vacancy.url)
    _apply(vacancy.posting, verdict, datetime.now(UTC))
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
    return or_(Posting.checked_at.is_(None), Posting.checked_at < now - FRESH_FOR)


async def _check_rows(rows: list[Posting], now: datetime) -> dict[str, int]:
    verdicts = await run_in_threadpool(_check_many, [v.url for v in rows])
    counts = {"checked": len(rows), "open": 0, "closed": 0, "unknown": 0}
    for row, verdict in zip(rows, verdicts, strict=True):
        _apply(row, verdict, now)
        counts[verdict.state] += 1
    return counts


def _in_play():
    """Postings at least one user still looks at: not hidden, and either
    saved or not dropped."""
    return select(Vacancy.posting_id).where(
        Vacancy.hidden_at.is_(None),
        or_(
            Vacancy.is_saved.is_(True),
            Vacancy.stage.notin_((SCREENED_OUT, TRIAGED_OUT)),
        ),
    )


def _read_feeds(boards: list[employers.Board]) -> tuple[list[Sighting], list[str]]:
    """Read the feeds whole. Network only. Returns (sightings, complete boards)."""
    # Feeds list everything the company has open; queries are not asked.
    result = employers.collect([], tuple({b.group for b in boards}), boards)
    group = {b.key: b.group for b in boards}
    sightings = [
        Sighting(
            p,
            text=result.texts.get(p.id),
            segment=group.get(employers.board_key(p.id)),
            needs_text=employers.needs_description(p.id) and not result.texts.get(p.id),
        )
        for p in result.postings
    ]
    return sightings, list(result.complete)


async def sweep_feeds(session: AsyncSession) -> dict[str, int]:
    """Read the ATS feeds whose postings someone has in play, and close the
    ones the company took down.

    A feed posting closes only by its absence from a full read of the feed
    (`pool.mark_absent`), and before this ran only a user's scan read it: a
    week without scans left the posting "давно не проверялась" and open.
    Free — HTTP only — and bounded to boards someone's list depends on.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Posting.external_id).where(
            Posting.id.in_(_in_play()),
            Posting.source == "company",
            Posting.closed_at.is_(None),
        )
    )
    wanted = {employers.board_key(ext) for ext in result.scalars()}
    hidden = set((await switches_db.load(session)).boards())
    boards = [
        b for b in employers.load()
        if b.kind in employers.FEED_KINDS and b.key in wanted and b.key not in hidden
    ]
    if not boards:
        return {"boards": 0, "complete": 0, "closed": 0}
    sightings, complete = await run_in_threadpool(_read_feeds, boards)
    # Refresh only what is pooled already. A new posting is left to the next
    # scan: the scan lane writes the pool at the same time, and two inserts
    # of one posting race on its unique key (see docs/decisions.md,
    # 2026-10-03, on why refresh shares the scan lane).
    known = set((await session.execute(
        select(Posting.external_id).where(
            Posting.source == "company",
            Posting.external_id.in_([s.posting.id for s in sightings]),
        )
    )).scalars())
    await pool.upsert(session, "company", [s for s in sightings if s.posting.id in known], now)
    closed = await pool.mark_absent(
        session, "company", complete, {s.posting.id for s in sightings}, now
    )
    return {"boards": len(boards), "complete": len(complete), "closed": closed}


async def sweep(session: AsyncSession, limit: int = MAX_PER_SWEEP) -> dict[str, int]:
    """One background pass over the postings some user still has in play.

    In play for at least one user: not hidden, and either saved or not
    dropped. A posting nobody looks at is not checked — the batch would be
    spent on what nobody reads. Closed ones ARE rechecked, because Habr
    brings archived postings back.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(Posting)
        .where(
            Posting.id.in_(_in_play()),
            Posting.source.in_(CHECKABLE_SOURCES),
            _due(now),
        )
        .order_by(Posting.checked_at.asc().nulls_first())
        .limit(limit * 2)
    )
    rows = [p for p in result.scalars() if liveness.can_check(p.url)][:limit]
    if not rows:
        return {"checked": 0, "open": 0, "closed": 0, "unknown": 0}
    return await _check_rows(rows, now)


def stale_reason(vacancy: Vacancy | Posting, now: datetime | None = None) -> str | None:
    """Why a row that cannot be asked counts as old, or None.

    Only for sources `core.liveness` cannot read: a checkable row has a real
    answer in `closed_at`, and age must not overrule it. Telegram is the
    exception: its answer is about the post, not the vacancy.

    An employer feed read whole has a real answer too — the company's list
    either still carries the posting or, after enough reads without it, the
    posting is closed (`pool.mark_absent`). So for a feed posting age is not
    the question; how long ago a read last saw it is.
    """
    now = now or datetime.now(UTC)
    if vacancy.source == "company" and employers.is_feed_posting(vacancy.external_id):
        posting = vacancy if isinstance(vacancy, Posting) else vacancy.posting
        unseen = now - posting.last_seen_at
        if unseen <= UNSEEN_AFTER:
            return None
        return f"не встречалась в ленте компании {unseen.days} дн., давно не проверялась"
    if vacancy.published is None:
        return None
    # A Telegram post can be asked, but only whether the POST is still there:
    # channels rarely delete, so a live post says nothing about the job.
    telegram = liveness.is_telegram_post(vacancy.url or "")
    if liveness.can_check(vacancy.url) and not telegram:
        return None
    age = now - vacancy.published
    if age <= STALE_AFTER:
        return None
    if telegram:
        return f"пост опубликован {age.days} дн. назад, канал не сообщает о закрытии"
    return f"опубликована {age.days} дн. назад, проверить у источника нельзя"


async def recheck(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """Check the postings the user is about to act on: saved, or still in play.

    Oldest check first, so repeated presses walk the whole list instead of
    re-asking about the same thirty. A posting another user's check answered
    recently counts as checked here too.
    """
    now = datetime.now(UTC)
    mine = select(Vacancy.posting_id).where(
        Vacancy.user_id == user_id,
        or_(
            Vacancy.is_saved.is_(True),
            and_(Vacancy.stage.in_((SHORTLIST, EVALUATED)), Vacancy.hidden_at.is_(None)),
        ),
    )
    result = await session.execute(
        select(Posting)
        .where(Posting.id.in_(mine), _due(now))
        .order_by(Posting.checked_at.asc().nulls_first())
    )
    rows = [p for p in result.scalars() if liveness.can_check(p.url)][:MAX_PER_RECHECK]
    return await _check_rows(rows, now)
