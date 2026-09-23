"""Collect postings and run the free stages, then persist what survived.

The pipeline invariant from CLAUDE.md, unchanged by the move to a database:

    collect (free) -> free filters (free) -> already-seen (free)
      -> triage (one cheap call per batch) -> full evaluation (paid)

Nothing here calls the expensive step. Evaluation is a separate request the
user makes per vacancy, which is what keeps a scan from spending money on a
list nobody asked to be scored.

Two demo rules that are easy to lose in a rewrite and are kept here:

  - A dropped posting is stored with its reason, not discarded. `stage` and
    `screen_reason` are the product's discard.log.
  - `raw_text` is never overwritten on a rescan. For Telegram it is the only
    copy of the posting, and different text means a different vacancy.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import dedup, tg_scan
from core.filters import PreScreen, SearchProfile
from core.llm import LLM, Ledger
from core.pipeline import build_search_profile
from core.sources import employers, geekjob, habr_api
from core.sources.habr_api import Posting

from ..models import Resume, User, Vacancy
from .spend import charge, ensure_can_spend

# Stages a vacancy can be in. Ordered by how far it got.
COLLECTED = "collected"
SCREENED_OUT = "screened_out"  # dropped by the free filters
TRIAGED_OUT = "triaged_out"  # dropped by the cheap batch call
SHORTLIST = "shortlist"  # survived everything free, worth paying for
EVALUATED = "evaluated"


def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# How a source is named in a duplicate's reason. Russian: the user reads it.
# With the preposition, which differs per source ("в Telegram", "на geekjob").
SOURCE_LABELS = {
    "habr": "в Хабр Карьере",
    "telegram": "в Telegram",
    "geekjob": "на geekjob",
    "company": "на сайте компании",
}

# Telegram reposts older than this are not compared by text: a vacancy
# reposted after a month is usually reopened, and worth a second look.
FINGERPRINT_WINDOW_DAYS = 30


class _Originals:
    """Every visible row of the user's that a new posting could duplicate.

    Loaded once per persist() and extended as rows are added, so two copies
    arriving in the same scan are caught as well as a copy of an old row.

    Only rows that are themselves visible count as originals. A row the free
    filters dropped is no reason to hide a new copy: the copy may come from a
    source with better fields and pass on its own merits — and marking it a
    duplicate of something the user never saw would hide it for good.
    """

    def __init__(self, rows: list[Vacancy]) -> None:
        self.by_key: dict[str, list[Vacancy]] = {}
        self.by_ref: dict[tuple[str, str], Vacancy] = {}
        self.with_fp: list[Vacancy] = []
        for row in rows:
            self.add(row)

    def add(self, row: Vacancy) -> None:
        if row.stage == SCREENED_OUT:
            return
        self.by_ref.setdefault((row.source, row.external_id), row)
        if row.dedup_key:
            self.by_key.setdefault(row.dedup_key, []).append(row)
        if row.text_fp:
            self.with_fp.append(row)

    def find(self, row: Vacancy, text: str) -> Vacancy | None:
        """The row this one copies, cheapest signal first."""
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


def _duplicate_reason(original: Vacancy) -> str:
    label = SOURCE_LABELS.get(original.source, f"в источнике {original.source}")
    return f"дубль: уже найдена {label} — «{original.title}»"[:512]


async def _originals(session: AsyncSession, user_id: uuid.UUID) -> _Originals:
    result = await session.execute(
        select(Vacancy).where(Vacancy.user_id == user_id, Vacancy.stage != SCREENED_OUT)
    )
    return _Originals(list(result.scalars()))


async def _existing(session: AsyncSession, user_id: uuid.UUID, source: str) -> dict[str, Vacancy]:
    """Everything already stored for this source, keyed by the source's own id."""
    result = await session.execute(
        select(Vacancy).where(Vacancy.user_id == user_id, Vacancy.source == source)
    )
    return {v.external_id: v for v in result.scalars()}


def _listing_fields(posting: Posting) -> dict[str, Any]:
    """What a rescan is allowed to refresh: listing data only.

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
        "location": posting.location or None,
        "remote": posting.remote,
        "published": _published(posting.published),
        "divisions": posting.divisions or None,
        "skills": posting.skills or None,
    }


async def persist(
    session: AsyncSession,
    user_id: uuid.UUID,
    source: str,
    kept: list[tuple[Posting, str]],
    dropped: list[tuple[Posting, str]],
    texts: dict[str, str] | None = None,
) -> dict[str, int]:
    """Write a scan's outcome. Returns counts, not rows.

    `texts` carries posting bodies that were collected along the way (Telegram),
    because there is no page to fetch them from later.
    """
    texts = texts or {}
    stored = await _existing(session, user_id, source)
    originals = await _originals(session, user_id)
    new_count = 0
    duplicates = 0

    for posting, reason in [(p, "") for p, _ in kept] + list(dropped):
        is_kept = not reason
        stage = SHORTLIST if is_kept else SCREENED_OUT
        text = texts.get(posting.id) or ""

        row = stored.get(posting.id)
        if row is None:
            row = Vacancy(
                # Explicit so a copy later in this same batch can point at it
                # before the flush assigns one.
                id=uuid.uuid4(),
                user_id=user_id,
                source=source,
                external_id=posting.id,
                stage=stage,
                screen_reason=reason or None,
                # Set once. A rescan must never overwrite it.
                raw_text=texts.get(posting.id),
                dedup_key=dedup.posting_key(posting) or None,
                text_fp=dedup.fingerprint(text) or None,
                **_listing_fields(posting),
            )
            # Only a posting the free filters kept is checked: a dropped one
            # already costs nothing and carries its own reason, which says
            # more about it than "duplicate" would.
            if is_kept:
                original = originals.find(row, text)
                if original is not None:
                    row.stage = SCREENED_OUT
                    row.screen_reason = _duplicate_reason(original)
                    row.duplicate_of_id = original.id
                    duplicates += 1
            session.add(row)
            stored[posting.id] = row
            originals.add(row)
            new_count += 1
            continue

        # Already known. Refresh the listing, leave everything else alone —
        # including a stage the user has already moved past.
        for field, value in _listing_fields(posting).items():
            if value is not None:
                setattr(row, field, value)
        # A duplicate stays one on a rescan: re-screening it alone would pass
        # it back to the shortlist and queue a second triage of a vacancy the
        # user already has.
        if row.duplicate_of_id is not None:
            continue
        # A free re-screen may revise its own verdict, never a paid one.
        # TRIAGED_OUT used to be in this list: a rescan then pushed the row
        # back to SHORTLIST and cleared `screen_reason` while `triage_verdict`
        # stayed "skip" — a red "мимо" tag with no reason under it, hidden by
        # the default bucket filter, and queued for another paid triage.
        if row.stage in (COLLECTED, SCREENED_OUT):
            row.stage = stage
            row.screen_reason = reason or None

    return {
        "new": new_count,
        "kept": len(kept) - duplicates,
        "dropped": len(dropped),
        "duplicates": duplicates,
    }


async def ensure_search_profile(
    session: AsyncSession, user: User, resume: Resume
) -> dict[str, Any]:
    """The resume's search profile, derived from the CV the first time.

    Every scan path goes through here. Keeping the derivation in one place is
    the point: a scan reached collect_habr from three callers (the jobs worker,
    POST /api/vacancies/scan, the CLI), and a fallback added to only some of
    them is how the same unfiltered run comes back through the door nobody
    patched.

    Costs one cheap call, once per resume, and is stored — a second scan
    reuses it. Uploading a CV derives it up front, so this fires only for CVs
    stored before that.
    """
    if resume.search_profile:
        return resume.search_profile

    # The only paid call on the scan path, so it is the only one that can slip
    # past a budget: every other paid step checks at its own entry point.
    ensure_can_spend(user)

    ledger = Ledger()
    llm = LLM(ledger=ledger)
    resume.search_profile = await run_in_threadpool(
        build_search_profile, llm, resume.profile
    )
    await charge(session, user, ledger)
    await session.commit()
    return resume.search_profile


def screen_postings(
    postings: list[Posting], profile: SearchProfile
) -> tuple[list[tuple[Posting, str]], list[tuple[Posting, str]]]:
    """Run the free checks. Returns (kept, dropped-with-reason).

    Every drop carries its reason out of here, because a screen that reports
    only a count is a screen nobody can tell from a bug.
    """
    screen = PreScreen(profile)
    kept: list[tuple[Posting, str]] = []
    dropped: list[tuple[Posting, str]] = []
    for posting in postings:
        decision = screen.check(posting)
        if decision.keep:
            kept.append((posting, ""))
        else:
            dropped.append((posting, decision.reason))
    return kept, dropped


@dataclass
class Batch:
    """One source's collect-and-screen result, before anything is written.

    Split from the write so every source's network can run at once while the
    writes stay one after another on one session (see `collect_all`).
    """

    source: str
    collected: int
    kept: list[tuple[Posting, str]]
    dropped: list[tuple[Posting, str]]
    texts: dict[str, str] = dc_field(default_factory=dict)
    # Source-specific facts worth showing: channels read, teasers enriched,
    # boards that failed.
    extra: dict[str, Any] = dc_field(default_factory=dict)


# How deep each source looks back. Not a user setting: the number meant
# something different per source (a Habr page is 25 postings per query, a
# Telegram page ~20 posts per channel, a geekjob page ~2 days of the feed)
# and nobody could tell what "5" bought. Depth costs time, never money, and
# history dedup means a regular scan only needs what appeared since the last.
HABR_PAGES = 3
TELEGRAM_PAGES = 1
GEEKJOB_PAGES = 5


def _profile(search_profile: dict[str, Any] | None, what: str) -> SearchProfile:
    if not search_profile:
        raise ValueError(f"профиль поиска не принят — подбор без него собрал бы {what}")
    return SearchProfile.from_dict(search_profile)


def fetch_habr(
    search_profile: dict[str, Any] | None,
    pages: int = HABR_PAGES,
    ask_habr_for_salary: bool = True,
) -> Batch:
    """Habr: fetch the catalogue, screen it for free. Network only, no DB.

    Refuses to run without an accepted search profile. `from_dict({})` builds a
    profile that states nothing, and nothing is not a filter: PreScreen keeps
    every posting (its title check is skipped when `positive` is empty) and the
    catalogue walk below would ask Habr for everything. The API refuses this
    earlier; the guard is repeated here because the worker calls straight into
    this path and an unfiltered scan is the one failure the free stage exists
    to prevent.
    """
    profile = _profile(search_profile, "весь каталог")
    # No empty-query fallback: "" asks Habr for the whole catalogue, which is
    # the same unfiltered flood by another route.
    if not profile.queries:
        raise ValueError("в профиле поиска нет ни одного запроса")

    # `salary_min` does two jobs, and they are not the same job. Sent to Habr
    # it drops every posting that states no range at all; kept in the profile
    # it feeds PreScreen step 6, which judges only postings that DO state one
    # and lets the silent ones through. Clearing it for both — the old
    # behaviour of the "искать без зарплаты" tick — meant a typed threshold
    # filtered nothing, while the hint promised it would.
    postings = habr_api.search_many(
        profile.queries,
        pages=pages,
        remote=profile.remote_only,
        qualifications=profile.qualifications,
        salary_from=profile.salary_min if ask_habr_for_salary else None,
    )
    kept, dropped = screen_postings(postings, profile)
    return Batch("habr", len(postings), kept, dropped)


def fetch_telegram(
    search_profile: dict[str, Any] | None,
    channels: list[str] | None = None,
    pages: int = TELEGRAM_PAGES,
    groups: tuple[str, ...] = ("core",),
) -> Batch:
    """Telegram, reusing the shared collector.

    The posting body is captured here and stored: t.me has no page to fetch a
    vacancy from later, so a Telegram row without its text can never be
    evaluated. This is the one source where losing raw_text loses the vacancy.

    `full_text()` rather than `.text`, so a teaser stores the description that
    was read from its link. Storing the bare post would throw the fetched page
    away at the last step and leave the evaluation judging four lines again.
    """
    result = tg_scan.collect(
        channels=channels, groups=groups, pages=pages, search_profile=search_profile
    )
    kept = [(tg_scan.as_posting(post), "") for post, _ in result["kept"]]
    dropped = [(tg_scan.as_posting(post), why) for post, why in result["dropped"]]
    texts = {
        post.id: post.full_text()
        for post, _ in result["kept"] + result["dropped"]
        if post.text
    }
    return Batch(
        "telegram",
        result["collected"],
        kept,
        dropped,
        texts,
        extra={
            "channels": result["channels"],
            # How many teasers gave up their description. Worth reporting: a
            # scan that read none of them is judging headlines.
            "enriched": result.get("enriched", 0),
            # Channels that answered but are unusable — silent, or gone.
            # Reported rather than swallowed: a scan that quietly returns
            # less is a scan nobody can debug.
            "problems": result["problems"],
        },
    )


def fetch_employers(
    search_profile: dict[str, Any] | None,
    groups: tuple[str, ...] = ("ru",),
) -> Batch:
    """Employers' own career sites (core/sources/employers.py).

    Searchable boards are asked the profile's queries; small ATS feeds are
    read whole and screened by the profile's keywords — so both guards below
    are needed, one per kind of board.

    Descriptions some boards do not list are fetched AFTER the free filters,
    for the survivors only, and stored: the row's text is what the evaluation
    reads, and a board page is not guaranteed to be there tomorrow.
    """
    profile = _profile(search_profile, "все вакансии")
    if not profile.queries or not profile.positive:
        raise ValueError("в профиле поиска нет запросов или ключевых слов")

    result = employers.collect(profile.queries, groups)
    kept, dropped = screen_postings(result.postings, profile)
    texts = dict(result.texts)
    texts.update(employers.describe([p for p, _ in kept]))
    return Batch(
        "company", len(result.postings), kept, dropped, texts,
        extra={"problems": result.problems},
    )


def fetch_geekjob(search_profile: dict[str, Any] | None, pages: int = GEEKJOB_PAGES) -> Batch:
    """geekjob.ru: the newest listing pages, screened for free.

    geekjob has no search we may use (robots.txt closes `/json/`, where its
    search box posts), so the newest pages are read whole and PreScreen does
    the filtering. That makes the keyword guard below matter more than for
    Habr: with no positive keywords every card on the board would pass.
    """
    profile = _profile(search_profile, "всю ленту")
    if not profile.positive:
        raise ValueError("в профиле поиска нет ключевых слов — geekjob сам не фильтрует")
    postings = geekjob.collect(pages)
    kept, dropped = screen_postings(postings, profile)
    return Batch("geekjob", len(postings), kept, dropped)


async def store(session: AsyncSession, user_id: uuid.UUID, batch: Batch) -> dict[str, Any]:
    counts = await persist(
        session, user_id, batch.source, batch.kept, batch.dropped, texts=batch.texts
    )
    return {"collected": batch.collected, **batch.extra, **counts}


# httpx.Client is synchronous, and a catalogue walk is seconds of network:
# calling it directly would stall every other request on the event loop.
# Hence every fetch goes through a thread.


async def collect_habr(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    pages: int = HABR_PAGES,
    ask_habr_for_salary: bool = True,
) -> dict[str, Any]:
    """Stage 1-2 for Habr. No model is called; survivors wait as `shortlist`."""
    batch = await run_in_threadpool(fetch_habr, search_profile, pages, ask_habr_for_salary)
    return await store(session, user_id, batch)


async def collect_telegram(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    channels: list[str] | None = None,
    pages: int = TELEGRAM_PAGES,
    groups: tuple[str, ...] = ("core",),
) -> dict[str, Any]:
    batch = await run_in_threadpool(fetch_telegram, search_profile, channels, pages, groups)
    return await store(session, user_id, batch)


async def collect_employers(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    groups: tuple[str, ...] = ("ru",),
) -> dict[str, Any]:
    batch = await run_in_threadpool(fetch_employers, search_profile, groups)
    return await store(session, user_id, batch)


async def collect_geekjob(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    pages: int = GEEKJOB_PAGES,
) -> dict[str, Any]:
    batch = await run_in_threadpool(fetch_geekjob, search_profile, pages)
    return await store(session, user_id, batch)


# Write order for a scan of everything. It decides which copy of a vacancy
# becomes the original and which the duplicate: sources with real fields
# (Habr's grade and division, an employer's own text) go first, so the row the
# user keeps is the richest one, and a Telegram retelling points at it.
ALL_SOURCES = ("habr", "company", "geekjob", "telegram")


async def collect_all(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    ask_habr_for_salary: bool = True,
    company_groups: tuple[str, ...] = ("ru",),
) -> dict[str, Any]:
    """Every source in one go: the network in parallel, the writes in order.

    A source that fails does not sink the others: its error is reported by
    name next to the counts of the ones that worked. The only error raised is
    the missing profile, which would fail every source identically.
    """
    _profile(search_profile, "все вакансии")
    fetches = {
        "habr": lambda: fetch_habr(search_profile, ask_habr_for_salary=ask_habr_for_salary),
        "company": lambda: fetch_employers(search_profile, company_groups),
        "geekjob": lambda: fetch_geekjob(search_profile),
        "telegram": lambda: fetch_telegram(search_profile),
    }
    results = await asyncio.gather(
        *(run_in_threadpool(fetches[name]) for name in ALL_SOURCES), return_exceptions=True
    )

    sources: dict[str, dict[str, Any]] = {}
    for name, result in zip(ALL_SOURCES, results, strict=True):
        if isinstance(result, BaseException):
            sources[name] = {"error": _error_text(result)}
            continue
        sources[name] = await store(session, user_id, result)

    ok = [s for s in sources.values() if "error" not in s]
    return {
        "sources": sources,
        "collected": sum(s["collected"] for s in ok),
        "kept": sum(s["kept"] for s in ok),
        "dropped": sum(s["dropped"] for s in ok),
        "duplicates": sum(s["duplicates"] for s in ok),
        "new": sum(s["new"] for s in ok),
    }


def _error_text(error: BaseException) -> str:
    if isinstance(error, ValueError):
        return str(error)
    return f"{type(error).__name__}: {error}"[:300]
