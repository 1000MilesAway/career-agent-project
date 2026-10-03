"""The shared pool of postings: written once, read by every user's scan.

A posting is a fact about a job board, not about whoever found it, so it is
fetched, stored, deduplicated and checked for liveness once for everyone
(docs/decisions.md, 2026-10-03). What a user decided about it lives on
`vacancies`; nothing here touches that table.

Two rules carried over from the per-user table, because a shared row makes
them matter more, not less:

  - `raw_text` is never overwritten by a later sighting. For Telegram it is
    the only copy, and different text means a different posting. The one
    later write is a teaser gaining the description it links to.
  - A refresh updates listing fields only. Everything bought hangs off the
    user's row, so a refresh cannot reach it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from core import dedup
from core.sources.habr_api import Posting

from ..models import Posting as Pooled
from ..models import SourceFetch

# How long a listing read from a source answers for everyone. A scan inside
# this window is matched from the pool without a single request; the next
# one after it reads the source again. Per source, by how fast it changes and
# how much a read costs the source: a Telegram channel page is cheap and
# moves hourly, an employer group is dozens of boards.
FRESH_FOR = {
    "habr": timedelta(minutes=30),
    "telegram": timedelta(minutes=20),
    "geekjob": timedelta(minutes=30),
    "company": timedelta(hours=2),
    # hh is read through a provider on one exit address that hh may ban, so
    # a query goes to hh at most once a day (decided 2026-10-03). Every scan
    # in between is answered from the pool.
    "hh": timedelta(days=1),
}

# A posting closes as taken down after this many full reads of its feed in a
# row without it, spread over at least this long. Both, because reads are
# not evenly spaced: two users' scans can read the same feed a minute apart,
# and three misses in ten minutes are one glitch seen three times.
ABSENT_READS = 3
ABSENT_FOR = timedelta(days=1)
ABSENT_REASON = "снята с сайта компании: нет в ленте вакансий"

# Telegram reposts older than this are not compared by text: a vacancy
# reposted after a month is usually reopened, and worth a second look.
FINGERPRINT_WINDOW_DAYS = 30


def listing_key(**parts: Any) -> str:
    """A stable name for one listing request: same parameters, same key."""
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    if len(raw) <= 512:
        return raw
    # Long query lists still need a key that fits the column.
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


async def known_keys(
    session: AsyncSession, source: str, keys: list[str]
) -> dict[str, SourceFetch]:
    """The keys of `source` some scan has ever read, fresh or not."""
    if not keys:
        return {}
    result = await session.execute(
        select(SourceFetch).where(SourceFetch.source == source, SourceFetch.key.in_(keys))
    )
    return {f.key: f for f in result.scalars()}


def is_fresh(fetch: SourceFetch, now: datetime) -> bool:
    """Read recently enough that a scan reuses it without a request."""
    return fetch.fetched_at > now - FRESH_FOR.get(fetch.source, timedelta(minutes=30))


async def mark_fetched(
    session: AsyncSession, source: str, key: str, now: datetime, extra: dict[str, Any] | None
) -> None:
    row = await session.get(SourceFetch, (source, key))
    if row is None:
        session.add(SourceFetch(source=source, key=key, fetched_at=now, extra=extra))
    else:
        row.fetched_at = now
        row.extra = extra


@dataclass
class Sighting:
    """One posting as a fetch returned it, before it is written."""

    posting: Posting
    # The body, when the listing carries one (Telegram, some boards).
    text: str | None = None
    # Which part of the source it came from; see `Posting.segment`.
    segment: str | None = None
    # The text still lacks a description fetched from elsewhere.
    needs_text: bool = False
    description_url: str | None = None


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def listing_fields(posting: Posting) -> dict[str, Any]:
    """What a later sighting is allowed to refresh: listing data only.

    Deliberately excludes raw_text, and excludes everything bought — score,
    report, artifacts live on other tables precisely so a refresh cannot
    reach them.
    """
    return {
        "url": posting.url,
        "title": posting.title,
        "company": posting.company,
        "qualification": posting.qualification or None,
        "salary_text": posting.salary_text or None,
        "salary_from": posting.salary_from,
        "salary_to": posting.salary_to,
        "currency": posting.currency or None,
        "location": posting.location or None,
        "remote": posting.remote,
        "accredited": posting.accredited,
        "published": _published(posting.published),
        "divisions": posting.divisions or None,
        "skills": posting.skills or None,
    }


def as_posting(row: Pooled) -> Posting:
    """A stored posting in the shape the free filters read.

    The inverse of `listing_fields`, so `PreScreen` judges a pooled posting
    exactly as it judged the fetched one.
    """
    return Posting(
        id=row.external_id,
        url=row.url or "",
        title=row.title,
        company=row.company or "",
        qualification=row.qualification or "",
        remote=row.remote,
        location=row.location or "",
        salary_from=row.salary_from,
        salary_to=row.salary_to,
        salary_text=row.salary_text or "",
        currency=row.currency or "",
        divisions=list(row.divisions or []),
        skills=list(row.skills or []),
        published=row.published.isoformat() if row.published else "",
        accredited=row.accredited,
    )


class _Originals:
    """Pooled postings a new one could copy, indexed by the three signals.

    Loaded per write from the pool — only what the incoming batch could hit —
    and extended as rows are added, so two copies in one batch are caught too.
    """

    def __init__(self, rows: list[Pooled]) -> None:
        self.by_key: dict[str, list[Pooled]] = {}
        self.by_ref: dict[tuple[str, str], Pooled] = {}
        self.with_fp: list[Pooled] = []
        for row in rows:
            self.add(row)

    def add(self, row: Pooled) -> None:
        self.by_ref.setdefault((row.source, row.external_id), row)
        if row.dedup_key:
            self.by_key.setdefault(row.dedup_key, []).append(row)
        if row.text_fp:
            self.with_fp.append(row)

    def find(self, row: Pooled, text: str) -> Pooled | None:
        """The posting this one copies, cheapest signal first."""
        for ref in dedup.linked_vacancies(text):
            original = self.by_ref.get(ref)
            if original is not None and original is not row:
                return original
        # Company + title counts only ACROSS sources. Within one board two ids
        # are two openings: a company runs "Python-разработчик" for two teams
        # at once, and calling the second a copy would hide a real vacancy.
        for original in self.by_key.get(row.dedup_key or "", []):
            if original.source != row.source:
                return original
        if row.text_fp:
            cutoff = datetime.now(UTC) - timedelta(days=FINGERPRINT_WINDOW_DAYS)
            for original in self.with_fp:
                if original is row:
                    continue
                if original.created_at and original.created_at < cutoff:
                    continue
                if dedup.similarity(row.text_fp, original.text_fp) >= dedup.SIMILARITY_THRESHOLD:
                    return original
        return None


async def _originals(
    session: AsyncSession, sightings: list[Sighting], keys: set[str]
) -> _Originals:
    refs = {ref for s in sightings for ref in dedup.linked_vacancies(s.text or "")}
    since = datetime.now(UTC) - timedelta(days=FINGERPRINT_WINDOW_DAYS)
    light = load_only(
        Pooled.id, Pooled.source, Pooled.external_id, Pooled.dedup_key, Pooled.text_fp,
        Pooled.duplicate_of_id, Pooled.created_at,
    )
    rows: dict[uuid.UUID, Pooled] = {}
    queries = [select(Pooled).where(Pooled.text_fp.is_not(None), Pooled.created_at >= since)]
    if keys:
        queries.append(select(Pooled).where(Pooled.dedup_key.in_(keys)))
    if refs:
        queries.append(
            select(Pooled).where(tuple_(Pooled.source, Pooled.external_id).in_(list(refs)))
        )
    for query in queries:
        for row in (await session.execute(query.options(light))).scalars():
            rows[row.id] = row
    return _Originals(list(rows.values()))


async def upsert(
    session: AsyncSession, source: str, sightings: list[Sighting], now: datetime | None = None
) -> dict[str, int]:
    """Write a fetch into the pool. Returns how many postings were new.

    A new posting that copies one already pooled — the same vacancy on
    another board, a repost in another channel — is stored all the same and
    pointed at the original; who sees which copy is decided per user.
    """
    now = now or datetime.now(UTC)
    if not sightings:
        return {"new": 0}
    ids = list({s.posting.id for s in sightings})
    result = await session.execute(
        select(Pooled).where(Pooled.source == source, Pooled.external_id.in_(ids))
    )
    stored = {p.external_id: p for p in result.scalars()}

    new_keys = {
        dedup.posting_key(s.posting) for s in sightings if s.posting.id not in stored
    } - {""}
    originals = await _originals(
        session, [s for s in sightings if s.posting.id not in stored], new_keys
    )

    new = 0
    for sighting in sightings:
        posting = sighting.posting
        text = sighting.text or ""
        row = stored.get(posting.id)
        if row is not None:
            # Already pooled. Refresh the listing, never the text.
            for field, value in listing_fields(posting).items():
                if value is not None:
                    setattr(row, field, value)
            if sighting.segment:
                row.segment = sighting.segment
            if row.raw_text is None and sighting.text:
                row.raw_text = sighting.text
            row.last_seen_at = now
            _seen_again(row)
            continue

        row = Pooled(
            # Explicit so a user row and a later copy in this same batch can
            # point at it before the flush assigns one.
            id=uuid.uuid4(),
            source=source,
            external_id=posting.id,
            segment=sighting.segment,
            # Set once. A later sighting must never overwrite it.
            raw_text=sighting.text,
            needs_text=sighting.needs_text,
            description_url=sighting.description_url,
            dedup_key=dedup.posting_key(posting) or None,
            text_fp=dedup.fingerprint(text) or None,
            last_seen_at=now,
            created_at=now,
            **listing_fields(posting),
        )
        original = originals.find(row, text)
        if original is not None:
            row.duplicate_of_id = original.duplicate_of_id or original.id
        session.add(row)
        stored[posting.id] = row
        originals.add(row)
        new += 1

    await session.flush()
    return {"new": new}


def _seen_again(row: Pooled) -> None:
    """A sighting clears the absence count, and reopens what absence closed.

    Only what absence closed: a 404 or an archive banner is evidence of its
    own, and a listing that still carries the posting does not overrule it.
    """
    if row.closed_at is not None and (row.closed_reason or "").startswith(ABSENT_REASON):
        row.closed_at = None
        row.closed_reason = None
    row.missed_reads = 0
    row.missing_since = None


async def mark_absent(
    session: AsyncSession,
    source: str,
    boards: list[str],
    seen: set[str],
    now: datetime | None = None,
) -> int:
    """Count a full read of `boards` against the postings it did not return.

    `boards` are feeds read whole (`employers.board_key`), `seen` the external
    ids that read returned. A posting of one of those boards missing from it
    gains a miss; after `ABSENT_READS` misses over at least `ABSENT_FOR` it is
    closed with the reason saying why. Returns how many were closed now.

    This is the one closing signal the employer sources have — their pages
    cannot be asked — and it is still positive evidence: the company's own
    list, read in full, no longer has the posting. Age is not used.
    """
    if not boards:
        return 0
    now = now or datetime.now(UTC)
    result = await session.execute(
        select(Pooled).where(
            Pooled.source == source,
            Pooled.closed_at.is_(None),
            or_(*(Pooled.external_id.startswith(f"{b}/") for b in boards)),
        )
    )
    closed = 0
    for row in result.scalars():
        if row.external_id in seen:
            continue
        row.missed_reads += 1
        row.missing_since = row.missing_since or now
        if row.missed_reads >= ABSENT_READS and now - row.missing_since >= ABSENT_FOR:
            row.closed_at = now
            row.closed_reason = (
                f"{ABSENT_REASON} {row.missed_reads} обхода подряд "
                f"с {row.missing_since:%d.%m}"
            )[:255]
            closed += 1
    return closed
