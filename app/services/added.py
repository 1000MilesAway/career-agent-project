"""Vacancies the user brings to «Избранное» by a link.

Two steps, both free. `preview` reads the link and pools the posting, so the
user sees what the link is before it becomes theirs; `add` then makes the
user's row for that pooled posting — saved, the same row a star on a scan's
find makes, so evaluation, materials and the tracker work on it unchanged.
The page is read once: `add` takes the posting the preview pooled, not the
link again.

What differs from a scan's row:

  - It carries `added_at`. A scan, "Очистить" and a profile change leave it
    alone: the user asked about this vacancy by name, which no filter overrules.
  - The free filters still run, for free, and their reason rides on the row:
    the user sees "грейд выше вашего" before paying to read the same in a
    report. They do not stop the user from evaluating it — that is their call.

Which links are read, and why no other: core/sources/user_added.py.
Pasted texts of before 2026-10-08 (source `text`) are the user's own: deleted
with the row, and with the account (`api/auth.py` `delete_me`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.filters import PreScreen
from core.sources import geekjob, user_added

from ..models import Posting as Pooled
from ..models import Resume, User, Vacancy
from . import pool
from .pool import Sighting
from .scan import FORM_OWNED, SCREENED_OUT, SHORTLIST, _profile

# Postings a scan pooled from employers' career sites (`scan.fetch_employers`).
# The only pool rows a link is looked up among without a request.
LISTED_SOURCE = "company"
# Pool rows a user may not take by id: a Telegram post can say anything, and a
# pasted text is someone else's own.
NOT_ADDABLE = (user_added.TEXT_SOURCE, "telegram", "telethon")


async def _listed(session: AsyncSession, url: str) -> Pooled | None:
    """The employer posting a scan pooled under this link, without any request."""
    found = await session.execute(
        select(Pooled)
        .where(Pooled.source == LISTED_SOURCE, Pooled.url.in_(user_added.url_variants(url)))
        .limit(1)
    )
    return found.scalar_one_or_none()


async def preview(session: AsyncSession, link: str) -> Pooled:
    """The pooled posting behind a link, read and pooled if new.

    ValueError says why not. Pooling before the user agrees is harmless: the
    pool is public listings, the same a scan writes for anyone.
    """
    link = (link or "").strip()
    try:
        item = await run_in_threadpool(user_added.from_url, link)
    except user_added.OtherSite:
        listed = await _listed(session, link)
        if listed is None:
            raise
        return listed
    except (httpx.HTTPError, geekjob.MarkupChanged) as e:
        raise ValueError(f"Не удалось прочитать страницу ({type(e).__name__}).") from e
    return await _pooled(session, item)


async def _pooled(session: AsyncSession, item: user_added.Added) -> Pooled:
    """The pooled posting for `item`, written if the pool lacks it.

    A posting a scan already pooled is not refreshed from the page: the
    listing a scan read carries fields the page does not (the grade, the
    accreditation), and a refresh would blank them. It only gains the text
    when it had none, and a closed mark the page states.
    """
    query = select(Pooled).where(
        Pooled.source == item.source, Pooled.external_id == item.posting.id
    )
    row = (await session.execute(query)).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None:
        await pool.upsert(session, item.source, [Sighting(item.posting, item.text)], now)
        row = (await session.execute(query)).scalar_one()
    elif row.raw_text is None:
        row.raw_text = item.text
        pool.mark_fraud(row)
    if item.closed_reason and row.closed_at is None:
        row.closed_at = now
        row.closed_reason = item.closed_reason[:255]
        row.checked_at = now
    return row


def _screen(resume: Resume | None, posting: Pooled) -> str:
    """The free filters' reason to drop it, or "" — shown, never acted on.

    The conditions the cabinet's form owns (salary, format, city) are left to
    the form, as in a rematch: they are applied on the screen, not stored.
    """
    if resume is None or not resume.search_profile:
        return ""
    try:
        profile = _profile({**resume.search_profile, **FORM_OWNED}, "")
    except ValueError:
        return ""
    decision = PreScreen(profile).check(pool.as_posting(posting))
    return "" if decision.keep else decision.reason


async def owned_row(session: AsyncSession, user: User, posting: Pooled) -> Vacancy | None:
    found = await session.execute(
        select(Vacancy).where(Vacancy.user_id == user.id, Vacancy.posting_id == posting.id)
    )
    return found.scalar_one_or_none()


async def add(
    session: AsyncSession, user: User, resume: Resume | None, posting_id: uuid.UUID
) -> tuple[Vacancy, bool]:
    """Save the posting a preview pooled. Returns the row and whether it is new.

    A vacancy the user already has — found by a scan, or added before — is
    not duplicated: that row is saved, keeping whatever was bought for it,
    and a hidden one comes back: bringing it by hand undoes "не интересно".
    """
    posting = await session.get(Pooled, posting_id)
    if posting is None or posting.source in NOT_ADDABLE:
        raise LookupError("vacancy not found")
    now = datetime.now(UTC)

    row = await owned_row(session, user, posting)
    created = row is None
    if row is None:
        reason = _screen(resume, posting)
        row = Vacancy(
            id=uuid.uuid4(),
            user_id=user.id,
            posting=posting,
            stage=SCREENED_OUT if reason else SHORTLIST,
            screen_reason=reason[:512] or None,
        )
        session.add(row)
    row.added_at = row.added_at or now
    row.hidden_at = None
    if not row.is_saved:
        row.is_saved = True
        row.saved_at = now
    await session.flush()
    return row, created


async def remove(session: AsyncSession, vacancy: Vacancy) -> None:
    """Delete an added vacancy with everything bought for it.

    SQL deletes, not `session.delete`: the ORM would lazy-load the reports to
    cascade them by hand, which async SQLAlchemy refuses; the database's own
    ON DELETE CASCADE does it in one statement. A pasted text is the user's
    alone, so its posting goes too; a public page stays in the pool.
    """
    posting = vacancy.posting
    await session.execute(delete(Vacancy).where(Vacancy.id == vacancy.id))
    if posting.source == user_added.TEXT_SOURCE:
        await session.execute(delete(Pooled).where(Pooled.id == posting.id))


async def delete_texts(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Every text this user pasted. Their rows cascade with the account; the
    postings hang off the pool, not the user, so they are deleted by owner."""
    await session.execute(
        delete(Pooled).where(
            Pooled.source == user_added.TEXT_SOURCE,
            Pooled.external_id.startswith(f"{user_id}:"),
        )
    )
