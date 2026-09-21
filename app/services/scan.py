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

import uuid
from datetime import datetime
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import tg_scan
from core.filters import PreScreen, SearchProfile
from core.llm import LLM, Ledger
from core.pipeline import build_search_profile
from core.sources import habr_api
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
    new_count = 0

    for posting, reason in [(p, "") for p, _ in kept] + list(dropped):
        is_kept = not reason
        stage = SHORTLIST if is_kept else SCREENED_OUT

        row = stored.get(posting.id)
        if row is None:
            row = Vacancy(
                user_id=user_id,
                source=source,
                external_id=posting.id,
                stage=stage,
                screen_reason=reason or None,
                # Set once. A rescan must never overwrite it.
                raw_text=texts.get(posting.id),
                **_listing_fields(posting),
            )
            session.add(row)
            stored[posting.id] = row
            new_count += 1
            continue

        # Already known. Refresh the listing, leave everything else alone —
        # including a stage the user has already moved past.
        for field, value in _listing_fields(posting).items():
            if value is not None:
                setattr(row, field, value)
        # A free re-screen may revise its own verdict, never a paid one.
        # TRIAGED_OUT used to be in this list: a rescan then pushed the row
        # back to SHORTLIST and cleared `screen_reason` while `triage_verdict`
        # stayed "skip" — a red "мимо" tag with no reason under it, hidden by
        # the default bucket filter, and queued for another paid triage.
        if row.stage in (COLLECTED, SCREENED_OUT):
            row.stage = stage
            row.screen_reason = reason or None

    return {"new": new_count, "kept": len(kept), "dropped": len(dropped)}


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


async def collect_habr(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    pages: int = 1,
    ask_habr_for_salary: bool = True,
) -> dict[str, Any]:
    """Stage 1-2 for Habr: fetch the catalogue, screen it for free.

    No model is called. What survives is stored as `shortlist` and waits for
    the user to ask for a triage or an evaluation. The argument list mirrors
    what the scan worker passes, so the two cannot drift.

    Refuses to run without an accepted search profile. `from_dict({})` builds a
    profile that states nothing, and nothing is not a filter: PreScreen keeps
    every posting (its title check is skipped when `positive` is empty) and the
    catalogue walk below would ask Habr for everything. The API refuses this
    earlier; the guard is repeated here because the worker calls straight into
    this function and an unfiltered scan is the one failure the free stage
    exists to prevent.
    """
    if not search_profile:
        raise ValueError(
            "профиль поиска не принят — подбор без него собрал бы весь каталог"
        )

    profile = SearchProfile.from_dict(search_profile)
    # httpx.Client is synchronous, and a catalogue walk is seconds of network:
    # calling it directly would stall every other request on the event loop.
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
    postings = await run_in_threadpool(
        habr_api.search_many,
        profile.queries,
        pages=pages,
        remote=profile.remote_only,
        qualifications=profile.qualifications,
        salary_from=profile.salary_min if ask_habr_for_salary else None,
    )

    kept, dropped = screen_postings(postings, profile)
    counts = await persist(session, user_id, "habr", kept, dropped)
    return {"collected": len(postings), **counts}


async def collect_telegram(
    session: AsyncSession,
    user_id: uuid.UUID,
    search_profile: dict[str, Any] | None,
    channels: list[str] | None = None,
    pages: int = 1,
    groups: tuple[str, ...] = ("core",),
) -> dict[str, Any]:
    """Stage 1-2 for Telegram, reusing the collector the CLI and demo use.

    The posting body is captured here and stored: t.me has no page to fetch a
    vacancy from later, so a Telegram row without its text can never be
    evaluated. This is the one source where losing raw_text loses the vacancy.
    """
    result = await run_in_threadpool(
        tg_scan.collect,
        channels=channels,
        groups=groups,
        pages=pages,
        search_profile=search_profile,
    )

    kept = [(tg_scan.as_posting(post), "") for post, _ in result["kept"]]
    dropped = [(tg_scan.as_posting(post), why) for post, why in result["dropped"]]
    texts = {post.id: post.text for post, _ in result["kept"] + result["dropped"] if post.text}

    counts = await persist(session, user_id, "telegram", kept, dropped, texts=texts)
    return {
        "collected": result["collected"],
        "channels": result["channels"],
        # Channels that answered but are unusable — silent, or gone. Reported
        # rather than swallowed: a scan that quietly returns less is a scan
        # nobody can debug.
        "problems": result["problems"],
        **counts,
    }
