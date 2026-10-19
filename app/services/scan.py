"""Collect postings into the shared pool, then match the pool to a user.

The pipeline invariant (.claude/rules/pipeline.md), unchanged by the move to a pool:

    collect (free) -> free filters (free) -> already-seen (free)
      -> triage (one cheap call per batch) -> full evaluation (paid)

Nothing here calls the expensive step. Evaluation is a separate request the
user makes per vacancy, which is what keeps a scan from spending money on a
list nobody asked to be scored.

Collecting is shared: a listing some scan read within `pool.FRESH_FOR` is
answered from `postings` without a request (docs/decisions.md, 2026-10-03).
Matching is per user and free: the user's `PreScreen` over the pool.

Rules that are easy to lose in a rewrite and are kept here:

  - A dropped posting is never silent. It gets no row of its own, but its
    reason is recomputed from the pool on request (`explain_screened`).
  - `raw_text` is never overwritten on a rescan. For Telegram it is the only
    copy of the posting, and different text means a different vacancy.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import tg_scan
from core.filters import PreScreen, SearchProfile
from core.llm import LLM, Ledger
from core.pipeline import build_search_profile
from core.sources import employers, geekjob, habr_api, hh, telegram, tg_channels, tg_chats
from core.sources.habr_api import Posting

from ..models import Application, Evaluation, Resume, User, Vacancy
from ..models import Posting as Pooled
from . import pool
from . import sources as switches_db
from .pool import Sighting
from .spend import charge, ensure_can_spend

# Stages a vacancy can be in. Ordered by how far it got.
COLLECTED = "collected"
SCREENED_OUT = "screened_out"  # dropped by the free filters
TRIAGED_OUT = "triaged_out"  # dropped by the cheap batch call
SHORTLIST = "shortlist"  # survived everything free, worth paying for
EVALUATED = "evaluated"


# How a source is named in a duplicate's reason. Russian: the user reads it.
# With the preposition, which differs per source ("в Telegram", "на geekjob").
SOURCE_LABELS = {
    "habr": "в Хабр Карьере",
    "hh": "на hh.ru",
    "telegram": "в Telegram",
    "geekjob": "на geekjob",
    "company": "на сайте компании",
    "telethon": "в Telegram-чате",
}

# Order sources are written and matched in. It decides which copy of a
# vacancy becomes the original and which the duplicate: sources with real
# fields (Habr's grade and division, an employer's own text) go first, so the
# row the user keeps is the richest one, and a Telegram retelling points at it.
ALL_SOURCES = ("habr", "hh", "company", "geekjob", "telegram", "telethon")

# Sources read through a provider on another host: asked only when it is set.
PROVIDERS = {"hh": hh.configured, "telethon": tg_chats.configured}


def active_sources() -> tuple[str, ...]:
    """The sources a scan of everything asks: providers only when they are set.

    Without HH_PROVIDER_URL every scan would carry an "hh: не настроен" error
    — on a laptop, in CI — for a source nobody switched on.
    """
    return tuple(s for s in ALL_SOURCES if s not in PROVIDERS or PROVIDERS[s]())

# Postings a scan matches: seen by some fetch this recently. One that dropped
# off every listing it was on ages out of new matches; rows a user already
# has stay. Two weeks covers a Telegram post that slid off the channel's
# first page and a Habr posting no query has returned since.
MATCH_WINDOW = timedelta(days=14)


def _duplicate_reason(original: Pooled) -> str:
    label = SOURCE_LABELS.get(original.source, f"в источнике {original.source}")
    return f"дубль: уже найдена {label} — «{original.title}»"[:512]


def _match_order(row: Pooled) -> tuple[int, int, datetime]:
    """Originals before copies, richer sources first, older first."""
    rank = ALL_SOURCES.index(row.source) if row.source in ALL_SOURCES else len(ALL_SOURCES)
    return (row.duplicate_of_id is not None, rank, row.created_at or datetime.now(UTC))


async def attach(
    session: AsyncSession, user_id: uuid.UUID, decided: list[tuple[Pooled, str]]
) -> dict[str, int]:
    """Give the user a row for each posting their free filters kept.

    `decided` pairs a pooled posting with the free filters' reason — empty
    for a keep. A dropped posting gets no row: its reason is recomputed from
    the pool when asked (`explain_screened`), so it is not lost, only not
    copied per user. A row the user already has is re-judged only while the
    verdict is free (`collected`, `screened_out`) — never a paid one.

    Copies of one vacancy reach the user once: a posting whose cluster the
    user already has a visible row for is counted as a duplicate. A copy whose
    original this user's filters drop is judged on its own merits — marking
    it a duplicate of something the user never saw would hide it for good.
    """
    result = await session.execute(select(Vacancy).where(Vacancy.user_id == user_id))
    mine = {v.posting_id: v for v in result.scalars()}
    clusters = {v.posting.cluster_id for v in mine.values() if v.stage != SCREENED_OUT}

    counts = {"new": 0, "kept": 0, "dropped": 0, "duplicates": 0}
    for posting, reason in sorted(decided, key=lambda d: _match_order(d[0])):
        row = mine.get(posting.id)
        if row is not None:
            # A free re-screen may revise its own verdict, never a paid one.
            # TRIAGED_OUT used to be in this list: a rescan then pushed the
            # row back to SHORTLIST and cleared `screen_reason` while
            # `triage_verdict` stayed "skip" — a red "мимо" tag with no reason
            # under it, and queued for another paid triage.
            if row.stage in (COLLECTED, SCREENED_OUT):
                row.stage = SCREENED_OUT if reason else SHORTLIST
                row.screen_reason = reason or None
            if reason:
                counts["dropped"] += 1
            else:
                counts["kept"] += 1
                clusters.add(posting.cluster_id)
            continue
        if reason:
            counts["dropped"] += 1
            continue
        if posting.cluster_id in clusters:
            counts["duplicates"] += 1
            continue
        row = Vacancy(id=uuid.uuid4(), user_id=user_id, posting=posting, stage=SHORTLIST)
        session.add(row)
        mine[posting.id] = row
        clusters.add(posting.cluster_id)
        counts["new"] += 1
        counts["kept"] += 1
    await session.flush()
    return counts


async def persist(
    session: AsyncSession,
    user_id: uuid.UUID,
    source: str,
    kept: list[tuple[Posting, str]],
    dropped: list[tuple[Posting, str]],
    texts: dict[str, str] | None = None,
) -> dict[str, int]:
    """Pool a fetch and attach what the given verdicts kept. Returns counts.

    The direct path for postings already screened by the caller; a scan goes
    through `collect`, which screens the pool itself. `texts` carries bodies
    collected along the way (Telegram), because there is no page to fetch
    them from later.
    """
    texts = texts or {}
    pairs = [(p, "") for p, _ in kept] + list(dropped)
    await pool.upsert(
        session, source, [Sighting(p, texts.get(p.id)) for p, _ in pairs]
    )
    result = await session.execute(
        select(Pooled).where(
            Pooled.source == source, Pooled.external_id.in_([p.id for p, _ in pairs])
        )
    )
    by_ext = {row.external_id: row for row in result.scalars()}
    return await attach(session, user_id, [(by_ext[p.id], why) for p, why in pairs])


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


# How deep each source looks back. Not a user setting: the number meant
# something different per source (a Habr page is 25 postings per query, a
# Telegram page ~20 posts per channel, a geekjob page ~2 days of the feed)
# and nobody could tell what "5" bought. Depth costs time, never money, and
# the pool means a regular scan only needs what appeared since the last.
HABR_PAGES = 3
TELEGRAM_PAGES = 1
GEEKJOB_PAGES = 5


def _profile(search_profile: dict[str, Any] | None, what: str) -> SearchProfile:
    if not search_profile:
        raise ValueError(f"профиль поиска не принят — подбор без него собрал бы {what}")
    return SearchProfile.from_dict(search_profile)


@dataclass
class Batch:
    """One listing's fetch, before anything is written. Network only, no DB.

    Unscreened: a fetch fills the pool for everyone, and each user's filters
    run over the pool afterwards. Split from the write so every source's
    network can run at once while the writes stay one after another on one
    session (see `collect`).
    """

    source: str
    sightings: list[Sighting]
    # Source-specific facts worth showing: channels read, boards that failed.
    extra: dict[str, Any] = dc_field(default_factory=dict)
    # Employer feeds this fetch read whole: what they did not return is gone
    # from the company's site (`pool.mark_absent`).
    complete: list[str] = dc_field(default_factory=list)


def fetch_habr(
    queries: list[str],
    pages: int = HABR_PAGES,
    remote: bool = False,
    qualifications: list[str] | None = None,
    salary_from: int | None = None,
) -> Batch:
    """Habr's catalogue for the given queries."""
    postings = habr_api.search_many(
        queries, pages=pages, remote=remote, qualifications=qualifications,
        salary_from=salary_from,
    )
    return Batch("habr", [Sighting(p) for p in postings])


def fetch_hh(query: str, remote: bool = False) -> Batch:
    """One query on hh.ru, through the hh-scrapper provider (core/sources/hh.py).

    The card's snippet is stored as the text, marked as still needing the
    description. That is read when a user asks for an evaluation — one request
    to hh for a vacancy someone wants, not one per card the filters drop — and
    replaces the snippet (`pool.is_snippet`).
    """
    cards, truncated = hh.search_cards(query, remote=remote)
    extra = {"truncated": [query]} if truncated else {}
    sightings = [
        Sighting(hh.as_posting(v), text=hh.card_text(v) or None, needs_text=True)
        for v in cards
    ]
    return Batch("hh", sightings, extra=extra)


def fetch_telegram(
    channels: list[str] | None,
    group: str | None,
    pages: int = TELEGRAM_PAGES,
    skip: frozenset[str] = frozenset(),
    found: tuple[str, ...] = (),
) -> Batch:
    """Channel pages through `t.me/s/`: the named channels, or one group's.

    The post body is stored: t.me has no page to fetch a vacancy from later,
    so a Telegram posting without its text can never be evaluated. A teaser's
    linked description is NOT read here — that is a request to someone else's
    host, and it is spent on the posts some user's filters keep (`_enrich`).
    """
    # A group's channels are the list file's plus those the discovery added
    # (services/channel_discovery.py) and the owner turned on.
    names = channels or [*tg_channels.channel_names(groups=(group or "core",)), *found]
    # Channels the owner hid are not asked at all (services/sources.py).
    names = [n for n in names if n not in skip]
    posts, problems = telegram.search_many(names, pages=pages)
    sightings = [
        Sighting(
            tg_scan.as_posting(post),
            text=post.full_text() or None,
            segment=None if channels else group,
            needs_text=bool(post.description_url),
            description_url=post.description_url or None,
        )
        for post in posts
    ]
    return Batch(
        "telegram",
        sightings,
        extra={
            "channels": names,
            # Channels that answered but are unusable — silent, or gone.
            # Reported rather than swallowed: a scan that quietly returns
            # less is a scan nobody can debug.
            "problems": [{"channel": c, "why": w} for c, w in problems],
        },
    )


def fetch_telethon(days: int = tg_chats.DAYS) -> Batch:
    """Telegram chats through the tg-provider userbot (core/sources/tg_chats.py).

    One read serves every user: the provider has already scanned its chats,
    and PreScreen does the filtering, as for a channel. The message is the
    whole vacancy, so its text is stored; a teaser's linked description is
    read later, for the posts some user's filters keep (`_enrich`).
    """
    posts, problems, chats = tg_chats.collect(days)
    sightings = [
        Sighting(
            tg_scan.as_posting(post),
            text=post.full_text() or None,
            needs_text=bool(post.description_url),
            description_url=post.description_url or None,
        )
        for post in posts
    ]
    return Batch(
        "telethon",
        sightings,
        extra={"chats": chats, "problems": [{"channel": c, "why": w} for c, w in problems]},
    )


def fetch_employers(queries: list[str], group: str, skip: frozenset[str] = frozenset()) -> Batch:
    """One group of employers' own career sites (core/sources/employers.py).

    Searchable boards are asked the queries; small ATS feeds are read whole.
    Descriptions some boards do not list are fetched later, for the postings
    some user's filters keep, and stored for everyone.
    """
    # Boards the owner hid are not asked at all (services/sources.py).
    boards = [b for b in employers.load() if b.key not in skip]
    result = employers.collect(queries, (group,), boards)
    sightings = [
        Sighting(
            p,
            text=result.texts.get(p.id),
            segment=group,
            needs_text=employers.needs_description(p.id) and not result.texts.get(p.id),
        )
        for p in result.postings
    ]
    return Batch(
        "company", sightings, extra={"problems": result.problems}, complete=result.complete
    )


def fetch_geekjob(pages: int = GEEKJOB_PAGES) -> Batch:
    """geekjob.ru: the newest listing pages.

    geekjob has no search we may use (robots.txt closes `/json/`, where its
    search box posts), so the newest pages are read whole and PreScreen does
    the filtering.
    """
    return Batch("geekjob", [Sighting(p) for p in geekjob.collect(pages)])


@dataclass
class Listing:
    """One request a scan may need: a Habr query, an employer group, a feed."""

    key: str
    fetch: Callable[[], Batch]


@dataclass
class Plan:
    """What one source contributes to a scan, and which pooled rows it matches."""

    source: str
    listings: list[Listing]
    # Match only postings from these segments (employer groups, channel
    # groups). None: the source has no segments.
    segments: tuple[str, ...] | None = None
    # Match only these Telegram channels, named explicitly (the id's prefix).
    channels: list[str] | None = None


@dataclass
class Options:
    """This scan's form settings that change what is fetched or matched."""

    ask_habr_for_salary: bool = True
    company_groups: tuple[str, ...] = ("ru",)
    telegram_groups: tuple[str, ...] = ("core",)
    channels: list[str] | None = None
    pages: int | None = None
    # Answer from the pool now and leave stale listings to a background
    # refresh (the worker's "refresh" job). A listing the pool has never read
    # is still fetched: there is nothing to answer it with.
    defer_stale: bool = False
    # Channels and boards the owner hid. Filled by `collect` from the
    # database; the listing keys leave them out, so a hide takes effect on
    # matching at once and on fetching from the next re-read.
    switches: switches_db.Switches = dc_field(default_factory=switches_db.Switches)


def plan(source: str, profile: SearchProfile, options: Options) -> Plan:
    """The listings `source` needs for this profile. Raises on an unusable profile.

    The guards are per source because the sources fail differently without
    them: Habr asked an empty query returns its whole catalogue, a feed read
    with no keywords passes every card.
    """
    if source == "habr":
        # No empty-query fallback: "" asks Habr for the whole catalogue.
        if not profile.queries:
            raise ValueError("в профиле поиска нет ни одного запроса")
        pages = options.pages or HABR_PAGES
        # `salary_min` does two jobs, and they are not the same job. Sent to
        # Habr it drops every posting that states no range at all; kept in the
        # profile it feeds PreScreen, which judges only postings that DO state
        # one and lets the silent ones through.
        salary = profile.salary_min if options.ask_habr_for_salary else None
        quals = sorted(profile.qualifications or [])
        return Plan("habr", [
            Listing(
                pool.listing_key(
                    q=q.strip().casefold(), pages=pages, remote=profile.remote_only,
                    quals=quals, salary=salary,
                ),
                lambda q=q: fetch_habr(
                    [q], pages=pages, remote=profile.remote_only,
                    qualifications=profile.qualifications, salary_from=salary,
                ),
            )
            for q in profile.queries
        ])
    if source == "hh":
        if not profile.queries:
            raise ValueError("в профиле поиска нет ни одного запроса")
        if not hh.configured():
            raise ValueError("hh.ru не подключён: не задан адрес провайдера")
        return Plan("hh", [
            Listing(
                pool.listing_key(q=q.strip().casefold(), remote=profile.remote_only),
                lambda q=q: fetch_hh(q, remote=profile.remote_only),
            )
            for q in profile.queries
        ])
    if source == "company":
        if not profile.queries or not profile.positive:
            raise ValueError("в профиле поиска нет запросов или ключевых слов")
        queries = sorted({q.strip().casefold() for q in profile.queries})
        return Plan("company", [
            Listing(
                pool.listing_key(group=g, queries=queries),
                lambda g=g: fetch_employers(
                    list(profile.queries), g, skip=frozenset(options.switches.boards())
                ),
            )
            for g in options.company_groups
        ], segments=options.company_groups)
    if source == "geekjob":
        if not profile.positive:
            raise ValueError("в профиле поиска нет ключевых слов — geekjob сам не фильтрует")
        pages = options.pages or GEEKJOB_PAGES
        return Plan("geekjob", [
            Listing(pool.listing_key(pages=pages), lambda: fetch_geekjob(pages)),
        ])
    if source == "telegram":
        pages = options.pages or TELEGRAM_PAGES
        if options.channels:
            channels = sorted(set(options.channels))
            return Plan("telegram", [
                Listing(
                    pool.listing_key(channels=channels, pages=pages),
                    lambda: fetch_telegram(
                        channels, None, pages, skip=frozenset(options.switches.channels())
                    ),
                ),
            ], channels=channels)
        return Plan("telegram", [
            Listing(
                pool.listing_key(group=g, pages=pages),
                lambda g=g: fetch_telegram(
                    None, g, pages, skip=frozenset(options.switches.channels()),
                    found=tuple(options.switches.found.get(g, ())),
                ),
            )
            for g in options.telegram_groups
        ], segments=options.telegram_groups)
    if source == "telethon":
        if not tg_chats.configured():
            raise ValueError("Telegram-чаты не подключены: не задан адрес провайдера")
        # Read whole, like geekjob's feed: without keywords PreScreen would
        # pass every vacancy posted to the chats.
        if not profile.positive:
            raise ValueError("в профиле поиска нет ключевых слов — чаты сами не фильтруют")
        return Plan("telethon", [
            Listing(pool.listing_key(days=tg_chats.DAYS), fetch_telethon),
        ])
    raise ValueError(f"неизвестный источник: {source}")


def _fetch_all(listings: list[Listing]) -> list[Batch]:
    """One source's stale listings, one after another, politely spaced."""
    out: list[Batch] = []
    for i, listing in enumerate(listings):
        if i:
            time.sleep(habr_api.DELAY_SECONDS)
        out.append(listing.fetch())
    return out


async def _candidates(
    session: AsyncSession, p: Plan, now: datetime, switches: switches_db.Switches
) -> list[Pooled]:
    """Pooled postings this source's scan matches: recent, open, in scope,
    and not from a channel or board the owner hid."""
    query = select(Pooled).where(
        Pooled.source == p.source,
        Pooled.last_seen_at >= now - MATCH_WINDOW,
        Pooled.closed_at.is_(None),
        switches.visible(p.source),
    )
    if p.segments is not None:
        # Rows pooled before segments were recorded carry none; they gain
        # one the next time a fetch returns them.
        query = query.where(or_(Pooled.segment.in_(p.segments), Pooled.segment.is_(None)))
    if p.channels is not None:
        query = query.where(func.split_part(Pooled.external_id, "/", 1).in_(p.channels))
    return list((await session.execute(query)).scalars())


# Linked descriptions read per scan. Free in money — HTTP, not the model —
# but each is a request to someone else's host, so it is capped.
MAX_ENRICHED = tg_scan.MAX_ENRICHED


def _read_descriptions(source: str, rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """Texts for (external_id, url, description_url) triples. Network only."""
    if source in ("telegram", "telethon"):
        posts = [
            telegram.Post(id=ext, url=url, channel="", text="", description_url=durl)
            for ext, url, durl in rows
        ]
        telegram.fetch_externals(posts, limit=MAX_ENRICHED)
        return {p.id: p.description_text for p in posts if p.description_text}
    if source == "company":
        stubs = [Posting(id=ext, url=url, title="", company="") for ext, url, _ in rows]
        return employers.describe(stubs)
    return {}


async def _enrich(session: AsyncSession, source: str, kept: list[Pooled]) -> int:
    """Fetch the descriptions kept postings still lack, once for everyone.

    After the free filters, over the survivors only: the pipeline's rule
    that the costly stage runs on the short list covers someone else's
    bandwidth as much as it covers paid calls. One attempt per posting —
    a page that failed is fetched again on evaluation, not on every scan.

    hh is left to the evaluation: its pages go through the provider's one
    address, where a burst would ban hh for every user at once.
    """
    if source in pool.SNIPPET_SOURCES:
        return 0
    todo = [p for p in kept if p.needs_text][:MAX_ENRICHED]
    if not todo:
        return 0
    texts = await run_in_threadpool(
        _read_descriptions,
        source,
        [(p.external_id, p.url or "", p.description_url or "") for p in todo],
    )
    for row in todo:
        row.needs_text = False
        text = texts.get(row.external_id)
        if not text:
            continue
        if source in ("telegram", "telethon"):
            # A teaser gains what it links to, labelled — the one write to
            # the text after creation (see Post.full_text).
            row.raw_text = (
                f"{row.raw_text or ''}\n\n"
                f"--- Полное описание со страницы {row.description_url} ---\n{text}"
            )
        elif row.raw_text is None:
            row.raw_text = text
        pool.mark_fraud(row)
    return sum(1 for row in todo if texts.get(row.external_id))


async def _match(
    session: AsyncSession,
    user_id: uuid.UUID,
    p: Plan,
    profile: SearchProfile,
    now: datetime,
    switches: switches_db.Switches,
) -> dict[str, Any]:
    rows = await _candidates(session, p, now, switches)
    screen = PreScreen(profile)
    decided = []
    for row in rows:
        decision = screen.check(pool.as_posting(row))
        decided.append((row, "" if decision.keep else decision.reason))
    counts = await attach(session, user_id, decided)
    enriched = await _enrich(session, p.source, [row for row, why in decided if not why])
    out: dict[str, Any] = {"collected": len(rows), **counts}
    if p.source in ("telegram", "telethon"):
        # How many teasers gave up their description. Worth reporting: a
        # scan that read none of them is judging headlines.
        out["enriched"] = enriched
    return out


async def collect(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    sources: tuple[str, ...] = ALL_SOURCES,
    options: Options | None = None,
    raise_errors: bool = False,
) -> dict[str, Any]:
    """Fetch what is stale, pool it, then match the pool against this user.

    Stage 1-2 of the pipeline: no model is called; survivors wait as
    `shortlist`. A listing some scan read within `pool.FRESH_FOR` is not read
    again — the pool answers. The network runs in parallel, the writes in
    `ALL_SOURCES` order on one session.

    A source that fails does not sink the others: its error is reported by
    name next to the counts of the ones that worked, unless `raise_errors`
    (one source asked for by name, where the error is the answer).
    """
    options = replace(options or Options(), switches=await switches_db.load(session))
    profile = _profile(search_profile, "все вакансии")
    now = datetime.now(UTC)

    plans: dict[str, Plan | BaseException] = {}
    for name in sources:
        try:
            plans[name] = plan(name, profile, options)
        except ValueError as e:
            if raise_errors:
                raise
            plans[name] = e

    stale: dict[str, list[Listing]] = {}
    deferred: dict[str, int] = {}
    cached: dict[str, dict[str, Any]] = {}
    for name, p in plans.items():
        if isinstance(p, BaseException):
            continue
        known = await pool.known_keys(session, name, [x.key for x in p.listings])
        # What the pool answers for: fresh listings, plus stale ones when
        # their re-read is left to the background refresh.
        reused = {
            k: f for k, f in known.items() if options.defer_stale or pool.is_fresh(f, now)
        }
        stale[name] = [x for x in p.listings if x.key not in reused]
        deferred[name] = sum(1 for f in reused.values() if not pool.is_fresh(f, now))
        cached[name] = _merge_extra([f.extra or {} for f in reused.values()])

    fetched = await asyncio.gather(
        *(run_in_threadpool(_fetch_all, stale[name]) for name in stale),
        return_exceptions=True,
    )
    batches = dict(zip(stale, fetched, strict=True))

    sources_out: dict[str, dict[str, Any]] = {}
    for name in sources:
        p = plans[name]
        if isinstance(p, BaseException):
            sources_out[name] = {"error": _error_text(p)}
            continue
        result = batches[name]
        if isinstance(result, BaseException):
            if raise_errors:
                raise result
            sources_out[name] = {"error": _error_text(result)}
            continue
        gone = 0
        for listing, batch in zip(stale[name], result, strict=True):
            await pool.upsert(session, name, batch.sightings, now)
            await pool.mark_fetched(session, name, listing.key, now, batch.extra or None)
            gone += await pool.mark_absent(
                session, name, batch.complete, {s.posting.id for s in batch.sightings}, now
            )
        counts = await _match(session, user_id, p, profile, now, options.switches)
        if name == "company":
            # Postings this read found taken down from the companies' sites.
            counts["closed"] = gone
        extra = _merge_extra([cached[name]] + [b.extra for b in result])
        sources_out[name] = {
            **counts, **extra,
            # How many of the listings came from the pool without a request.
            "from_pool": len(p.listings) - len(stale[name]),
            "fetched": len(stale[name]),
            # Of those, how many are past their freshness and wait for the
            # background refresh.
            "deferred": deferred[name],
        }

    ok = [s for s in sources_out.values() if "error" not in s]
    return {
        "sources": sources_out,
        "collected": sum(s["collected"] for s in ok),
        "kept": sum(s["kept"] for s in ok),
        "dropped": sum(s["dropped"] for s in ok),
        "duplicates": sum(s["duplicates"] for s in ok),
        "new": sum(s["new"] for s in ok),
        "deferred": sum(s["deferred"] for s in ok),
    }


def _merge_extra(extras: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for extra in extras:
        for key, value in extra.items():
            if isinstance(value, list):
                out.setdefault(key, [])
                out[key] += [v for v in value if v not in out[key]]
            else:
                out[key] = value
    return out


async def collect_habr(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    pages: int = HABR_PAGES,
    ask_habr_for_salary: bool = True,
) -> dict[str, Any]:
    """Habr alone. Errors are raised: the source was asked for by name."""
    result = await collect(
        session, user_id, search_profile, ("habr",),
        Options(ask_habr_for_salary=ask_habr_for_salary, pages=pages), raise_errors=True,
    )
    return result["sources"]["habr"]


async def collect_telegram(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    channels: list[str] | None = None,
    pages: int = TELEGRAM_PAGES,
    groups: tuple[str, ...] = ("core",),
) -> dict[str, Any]:
    result = await collect(
        session, user_id, search_profile, ("telegram",),
        Options(channels=channels, pages=pages, telegram_groups=groups), raise_errors=True,
    )
    return result["sources"]["telegram"]


async def collect_employers(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    groups: tuple[str, ...] = ("ru",),
) -> dict[str, Any]:
    result = await collect(
        session, user_id, search_profile, ("company",),
        Options(company_groups=groups), raise_errors=True,
    )
    return result["sources"]["company"]


async def collect_geekjob(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    pages: int = GEEKJOB_PAGES,
) -> dict[str, Any]:
    result = await collect(
        session, user_id, search_profile, ("geekjob",), Options(pages=pages), raise_errors=True,
    )
    return result["sources"]["geekjob"]


async def collect_hh(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    result = await collect(session, user_id, search_profile, ("hh",), raise_errors=True)
    return result["sources"]["hh"]


async def collect_all(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    ask_habr_for_salary: bool = True,
    company_groups: tuple[str, ...] = ("ru",),
    telegram_groups: tuple[str, ...] = ("core",),
    defer_stale: bool = False,
) -> dict[str, Any]:
    """Every source in one go. The only error raised is the missing profile,
    which would fail every source identically."""
    _profile(search_profile, "все вакансии")
    return await collect(
        session, user_id, search_profile, active_sources(),
        Options(
            ask_habr_for_salary=ask_habr_for_salary,
            company_groups=company_groups,
            telegram_groups=telegram_groups,
            defer_stale=defer_stale,
        ),
    )


# The scan form sends these with every scan (`with_overrides`), so what the
# stored profile says about them never decided a row; the cabinet filters the
# list by the same conditions on render. A rematch that read the stored values
# would drop rows the user's own form lets through.
FORM_OWNED: dict[str, Any] = {
    "salary_min": None, "remote_only": False, "accredited_only": False, "cities": [],
}

# Reason kinds a rematch reports, most frequent first. The full reason of
# each posting is in `explain_screened`; the toast needs only the gist.
REMATCH_REASONS = 3


def _untouched(user_id: uuid.UUID):
    """The user's rows nothing but a free filter put there.

    A triage verdict, an evaluation, a tracker stage, a save, a hide or a
    vacancy the user added by hand is the user's investment (or money spent on their behalf); a profile change must
    not take any of those away.
    """
    return select(Vacancy).where(
        Vacancy.user_id == user_id,
        Vacancy.stage.in_((COLLECTED, SHORTLIST)),
        Vacancy.triage_verdict.is_(None),
        Vacancy.is_saved.is_(False),
        Vacancy.hidden_at.is_(None),
        Vacancy.added_at.is_(None),
        ~exists().where(Evaluation.vacancy_id == Vacancy.id),
        ~exists().where(Application.vacancy_id == Vacancy.id),
    )


async def rematch(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    sources: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Bring the user's found list in line with a changed search profile.

    Free and offline: the free filters over the pool and over the rows the
    user has, no request to any source and no model. Untouched rows the new
    profile drops are removed — their reasons stay reachable through
    `explain_screened`, so the drop is not silent — and pooled postings it
    keeps are added, as a scan would. Rows the user acted on stay as they are.
    """
    profile = _profile({**(search_profile or {}), **FORM_OWNED}, "все вакансии")
    screen = PreScreen(profile)

    reasons: Counter[str] = Counter()
    for row in (await session.execute(_untouched(user_id))).scalars():
        decision = screen.check(pool.as_posting(row.posting))
        if not decision.keep:
            # The kind, not the detail: "стоп-слово в заголовке: 1С" and
            # "...: SAP" are one cause to the person reading the toast.
            reasons[decision.reason.split(":")[0]] += 1
            await session.delete(row)
    await session.flush()

    options = Options(switches=await switches_db.load(session))
    now = datetime.now(UTC)
    added = 0
    for name in sources or active_sources():
        try:
            p = plan(name, profile, options)
        except ValueError:
            # The same profile fails the same way on the next scan, which
            # says so per source; a rematch has nothing to add for it.
            continue
        rows = await _candidates(session, p, now, options.switches)
        decided = []
        for row in rows:
            decision = screen.check(pool.as_posting(row))
            decided.append((row, "" if decision.keep else decision.reason))
        added += (await attach(session, user_id, decided))["new"]

    return {
        "added": added,
        "removed": sum(reasons.values()),
        "reasons": [
            {"reason": r, "count": n} for r, n in reasons.most_common(REMATCH_REASONS)
        ],
    }


# Rows `explain_screened` returns at most. The pool is thousands of postings;
# the reasons are read to check a filter, not to browse the whole board.
SCREENED_SHOWN = 300


async def explain_screened(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    limit: int = SCREENED_SHOWN,
) -> list[dict[str, Any]]:
    """What this user's free filters drop from the pool, each with its reason.

    The discard log, computed instead of stored: the filters are free and
    deterministic, so running them again gives the same reason the scan had.
    Covers postings a scan would match now and the user has no row for —
    including copies of vacancies the user already has, named as such.
    """
    profile = _profile(search_profile, "все вакансии")
    now = datetime.now(UTC)
    result = await session.execute(select(Vacancy).where(Vacancy.user_id == user_id))
    mine = {v.posting_id: v for v in result.scalars()}
    clusters = {v.posting.cluster_id for v in mine.values() if v.stage != SCREENED_OUT}

    switches = await switches_db.load(session)
    # A hidden channel or board is not a filter's drop: the owner turned the
    # source off, as if it were taken out of the list file.
    query = select(Pooled).where(
        Pooled.last_seen_at >= now - MATCH_WINDOW,
        Pooled.closed_at.is_(None),
        switches.visible_any(),
    ).order_by(Pooled.last_seen_at.desc())
    rows = [r for r in (await session.execute(query)).scalars() if r.id not in mine]

    originals = {}
    copies = [r for r in rows if r.duplicate_of_id is not None]
    if copies:
        found = await session.execute(
            select(Pooled).where(Pooled.id.in_({r.duplicate_of_id for r in copies}))
        )
        originals = {o.id: o for o in found.scalars()}

    screen = PreScreen(profile)
    out: list[dict[str, Any]] = []
    for row in rows:
        decision = screen.check(pool.as_posting(row))
        if decision.keep:
            if row.cluster_id not in clusters or row.duplicate_of_id not in originals:
                continue
            reason = _duplicate_reason(originals[row.duplicate_of_id])
        else:
            reason = decision.reason
        out.append({
            "posting_id": str(row.id),
            "source": row.source,
            "url": row.url,
            "title": row.title,
            "company": switches.company(row.company),
            "salary_text": row.salary_text,
            "reason": reason,
        })
        if len(out) >= limit:
            break
    return out


class ScanRequest(BaseModel):
    # "all" is what the cabinet sends: one button, every source. The single
    # sources stay for the API and for debugging one board.
    source: Literal["all", "habr", "hh", "telegram", "geekjob", "company", "telethon"] = "habr"
    # Depth override for a single source. None = the source's own default
    # (services/scan.py). Not shown in the cabinet: a "page" meant something
    # different per source and told the user nothing.
    pages: int | None = Field(default=None, ge=1, le=20)
    # Employers abroad hiring Russian speakers (the "global" boards) — English
    # postings, off by default.
    include_global: bool = False
    channels: list[str] | None = None
    # "и смежные каналы" on the scan form. The checkbox existed but was read
    # by nobody, so adjacent channels were unreachable from the product.
    groups: list[str] = Field(default_factory=lambda: ["core"])

    # Overrides for this run only; the stored search profile is not touched.
    # `salary_min` of 0 means "no threshold", which is not the same as None:
    # None leaves whatever the profile says, 0 clears it for this scan.
    salary_min: int | None = Field(default=None, ge=0, le=100_000_000)
    # Habr returns only postings WITH a stated range once a threshold is sent,
    # so a threshold silently hides every vacancy that does not publish
    # numbers. Keeping those is the default: the free filters judge them by
    # title anyway, and a hidden vacancy cannot be judged at all.
    any_salary: bool = True
    remote_only: bool | None = None
    # Not sent to any source: Habr wants its own city ids and hh its area
    # codes, and a listing keyed by city would stop being shared across users
    # in the pool. PreScreen drops the other cities here and says which.
    # None leaves the profile's list; [] clears it for this run.
    cities: list[str] | None = Field(default=None, max_length=20)
    # Not sent to Habr as `has_accreditation`: what Habr filters out never
    # reaches us, and a vacancy hidden without a reason is the one thing the
    # free stage must not do. PreScreen drops it here and says why.
    accredited_only: bool | None = None


def with_overrides(search_profile: dict[str, Any], body: ScanRequest) -> dict[str, Any]:
    """The stored profile plus this run's form settings.

    A copy: the form is a per-scan choice, and writing it back would let a
    stray click redefine the profile the user keeps.
    """
    merged = dict(search_profile)

    # The form owns the threshold outright, including "cleared": leaving the
    # stored value in place when the field is empty meant the user wiped the
    # box and kept filtering by a number no screen ever showed them.
    #
    # `any_salary` does NOT clear it — that was the bug. It only decides
    # whether Habr is asked for a floor (which hides every posting with no
    # stated range); PreScreen keeps judging the ones that do state a range.
    merged["salary_min"] = body.salary_min or None

    if body.remote_only is not None:
        merged["remote_only"] = body.remote_only
    if body.accredited_only is not None:
        merged["accredited_only"] = body.accredited_only
    if body.cities is not None:
        merged["cities"] = [c.strip() for c in body.cities if c.strip()]
    return merged


async def run_scan(
    session: AsyncSession,
    user: User,
    resume: Resume,
    body: ScanRequest,
    defer_stale: bool = False,
) -> dict[str, Any]:
    """One scan as the user asked for it: profile, this run's form, the sources.

    Shared by POST /api/vacancies/scan and the jobs worker, so the button and
    the API cannot drift on what a scan means. Does not commit — the caller
    owns the transaction. Raises ValueError for an unusable profile and lets a
    single board's network or markup failure through; a scan of everything
    reports those per source instead.

    `defer_stale` answers a scan of everything from the pool and reports how
    many listings were past their freshness (`deferred`); the worker reads
    those in a follow-up job. A single source asked for by name is always
    read now — that request is for debugging one board, not for speed.
    """
    search_profile = await ensure_search_profile(session, user, resume)
    search_profile = with_overrides(search_profile, body)

    company_groups = ("ru", "global") if body.include_global else ("ru",)

    if body.source == "all":
        return await collect_all(
            session, user.id, search_profile,
            ask_habr_for_salary=not body.any_salary,
            company_groups=company_groups,
            telegram_groups=tuple(body.groups),
            defer_stale=defer_stale,
        )
    if body.source == "habr":
        return await collect_habr(
            session, user.id, search_profile, pages=body.pages or HABR_PAGES,
            ask_habr_for_salary=not body.any_salary,
        )
    if body.source == "hh":
        return await collect_hh(session, user.id, search_profile)
    if body.source == "geekjob":
        return await collect_geekjob(
            session, user.id, search_profile, pages=body.pages or GEEKJOB_PAGES,
        )
    if body.source == "company":
        return await collect_employers(session, user.id, search_profile, company_groups)
    if body.source == "telethon":
        result = await collect(
            session, user.id, search_profile, ("telethon",), raise_errors=True
        )
        return result["sources"]["telethon"]
    return await collect_telegram(
        session, user.id, search_profile,
        channels=body.channels, pages=body.pages or TELEGRAM_PAGES,
        groups=tuple(body.groups),
    )


def _error_text(error: BaseException) -> str:
    if isinstance(error, ValueError):
        return str(error)
    return f"{type(error).__name__}: {error}"[:300]
