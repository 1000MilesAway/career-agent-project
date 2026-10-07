"""Vacancies the user brought themselves: «Мои вакансии».

A link or a pasted text becomes a posting and the user's row for it, the same
pair a scan makes, so evaluation, materials, the shortlist and the tracker
work on it unchanged. What differs:

  - The row carries `added_at` and lives in its own section, not in the
    search list. A scan, "Очистить" and a profile change leave it alone: the
    user asked about this vacancy by name, which no filter overrules.
  - The free filters still run, for free, and their reason rides on the row:
    the user sees "грейд выше вашего" before paying to read the same in a
    report. They do not stop the user from evaluating it — that is their call.
  - Nothing is bought here. Adding is free; the evaluation is its own click.

Pasted text never joins the shared pool's matching: its source (`text`) is
not one any scan reads, its id carries the owner's id, and it gets no dedup
keys, so no other user's posting can be filed as a copy of it. It is deleted
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


async def read(raw: str, owner: uuid.UUID) -> user_added.Added:
    """The vacancy behind a link or a text. ValueError says what went wrong."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Вставьте ссылку на вакансию или её текст.")
    if not user_added.is_link(raw):
        return user_added.from_text(raw, str(owner))
    try:
        return await run_in_threadpool(user_added.from_url, raw)
    except (httpx.HTTPError, geekjob.MarkupChanged) as e:
        raise ValueError(
            f"Не удалось прочитать страницу ({type(e).__name__}). "
            "Скопируйте текст вакансии и вставьте его сюда."
        ) from e


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
        if item.source == user_added.TEXT_SOURCE:
            row = Pooled(
                id=uuid.uuid4(),
                source=item.source,
                external_id=item.posting.id,
                raw_text=item.text,
                last_seen_at=now,
                created_at=now,
                **pool.listing_fields(item.posting),
            )
            pool.mark_fraud(row)
            session.add(row)
            await session.flush()
        else:
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


async def add(
    session: AsyncSession, user: User, resume: Resume | None, raw: str
) -> tuple[Vacancy, bool]:
    """Add a vacancy by link or text. Returns the row and whether it is new.

    A vacancy the user already has — found by a scan, or added before — is
    not duplicated: that row moves to «Мои вакансии», keeping whatever was
    bought for it. A hidden one comes back: bringing it by hand undoes
    "не интересно".
    """
    item = await read(raw, user.id)
    posting = await _pooled(session, item)
    now = datetime.now(UTC)

    found = await session.execute(
        select(Vacancy).where(Vacancy.user_id == user.id, Vacancy.posting_id == posting.id)
    )
    row = found.scalar_one_or_none()
    if row is not None:
        row.added_at = row.added_at or now
        row.hidden_at = None
        await session.flush()
        return row, False

    reason = _screen(resume, posting)
    row = Vacancy(
        id=uuid.uuid4(),
        user_id=user.id,
        posting=posting,
        stage=SCREENED_OUT if reason else SHORTLIST,
        screen_reason=reason[:512] or None,
        added_at=now,
    )
    session.add(row)
    await session.flush()
    return row, True


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
