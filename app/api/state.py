"""One call that answers "what should the screen show right now".

The demo's screen was built against a single `/api/state`, and keeping that
shape is deliberate: the alternative was five round-trips on every page load
and a rewrite of the client for no user-visible gain.

Free — nothing here calls the model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import joinedload, load_only, selectinload

from core.filters import rouble_band
from core.profiles import steps_as_list

from ..core.deps import SessionDep, UserDep
from ..models import Evaluation, Posting, Resume, Vacancy
from ..services import liveness, revisions, sources
from ..services.spend import quota
from ..services.tracker import STATUSES

router = APIRouter(prefix="/api", tags=["state"])


def salary_band(posting: Posting) -> tuple[int | None, int | None]:
    """The stored band as rouble numbers, for the cabinet's own filter.

    Read from the pool's columns by the same rule `PreScreen` judges with,
    so the conditions above the list and the scan cannot disagree. A band in
    another currency, or none stated, is (None, None): the cabinet then
    treats it as "no salary stated" — the same pass `PreScreen` gives it.
    """
    return rouble_band(posting.salary_from, posting.salary_to, posting.currency)

# Versions listed under "История версий". Older ones stay in the database —
# they back bought reports — but a list longer than this is not read.
HISTORY_SHOWN = 20


async def _resume_history(session: SessionDep, user_id: Any) -> list[dict[str, Any]]:
    """Every version of the CV, newest first, with what each one changed.

    Lives in the state payload rather than behind its own endpoint: the page
    reads one SWR key, and a rollback revalidates it in one call.
    """
    rows = await session.execute(
        select(Resume).where(Resume.user_id == user_id).order_by(Resume.created_at.desc())
    )
    resumes = list(rows.scalars())
    by_id = {r.id: r for r in resumes}
    return [
        {
            "id": str(r.id),
            "revision": r.revision,
            "filename": r.filename,
            "created_at": r.created_at.isoformat(),
            "active": r.is_active,
            "changes": revisions.changes(r, by_id.get(r.parent_id) if r.parent_id else None),
        }
        for r in resumes[:HISTORY_SHOWN]
    ]


@router.get("/state")
async def state(session: SessionDep, user: UserDep) -> dict[str, Any]:
    """Resume, findings and spend in one payload."""
    resume_row = await session.execute(
        select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True))
    )
    resume = resume_row.scalar_one_or_none()

    vacancies_row = await session.execute(
        select(Vacancy)
        .where(Vacancy.user_id == user.id)
        .options(
            # Only what the payload carries. `raw_text` is the posting itself —
            # megabytes for a few thousand rows — and was read on every call
            # only to be dropped. raiseload: a field added to the payload but
            # not here fails loudly instead of lazy-loading row by row.
            load_only(
                Vacancy.id,
                Vacancy.stage,
                Vacancy.screen_reason,
                Vacancy.triage_verdict,
                Vacancy.is_saved,
                Vacancy.hidden_at,
                Vacancy.created_at,
                raiseload=True,
            ),
            joinedload(Vacancy.posting).load_only(
                Posting.id,
                Posting.url,
                Posting.title,
                Posting.company,
                Posting.qualification,
                Posting.salary_text,
                Posting.salary_from,
                Posting.salary_to,
                Posting.currency,
                Posting.fraud_reason,
                Posting.remote,
                Posting.location,
                Posting.accredited,
                Posting.source,
                # Read by `liveness.stale_reason`: an employer feed posting is
                # judged by when a read last saw it, not by its age.
                Posting.external_id,
                Posting.last_seen_at,
                Posting.closed_at,
                Posting.closed_reason,
                Posting.published,
                raiseload=True,
            ),
            selectinload(Vacancy.evaluations).selectinload(Evaluation.artifacts),
            selectinload(Vacancy.application),
        )
        .order_by(Vacancy.created_at.desc())
    )
    vacancies = list(vacancies_row.scalars())
    switches = await sources.load(session)

    now = datetime.now(UTC)
    found = []
    scored = 0
    with_artifacts = 0

    for v in vacancies:
        # A channel or board the owner hid leaves the results, but not what
        # the user made theirs: a saved vacancy, a bought report, a tracked one.
        if (
            switches.is_hidden(v.source, v.external_id, v.company)
            and not v.is_saved
            and not v.evaluations
            and v.application is None
        ):
            continue
        evaluation = next(
            (e for e in v.evaluations if resume and e.resume_id == resume.id),
            v.evaluations[0] if v.evaluations else None,
        )
        artifacts: dict[str, Any] = {}
        if evaluation:
            scored += 1
            for a in evaluation.artifacts:
                artifacts[a.kind] = a.content
        if artifacts:
            with_artifacts += 1

        found.append(
            {
                "id": str(v.id),
                "url": v.url,
                "title": v.title,
                "company": switches.company(v.company),
                "qualification": v.qualification,
                "salary_text": v.salary_text,
                # The conditions above the list apply to rows already found,
                # not only to the next scan; the threshold needs a number.
                "salary_range": salary_band(v.posting),
                # A mark with its reason, never a drop (core/fraud.py).
                "fraud": v.posting.fraud_reason,
                "remote": v.remote,
                "location": v.location,
                "accredited": v.accredited,
                "source": v.source,
                "stage": v.stage,
                # A dropped posting keeps its reason and stays on screen: the
                # user has to be able to tell a working filter from an
                # over-eager one.
                "screen_reason": v.screen_reason,
                "triage": (
                    {"bucket": v.triage_verdict, "reason": v.screen_reason}
                    if v.triage_verdict
                    else None
                ),
                "saved": v.is_saved,
                "status": v.application.status if v.application else None,
                "score": evaluation.score if evaluation else None,
                "verdict": evaluation.verdict if evaluation else None,
                "report": evaluation.report if evaluation else None,
                "artifacts": artifacts or None,
                # The report on screen was bought against an earlier CV or an
                # earlier revision of it. Shown rather than hidden — it was
                # paid for — but marked, so the user knows a fresh score is
                # one click away and what the old one was judged against.
                "outdated": bool(
                    evaluation and resume and evaluation.resume_id != resume.id
                ),
                # Copies of a vacancy the user already has get no row of
                # their own any more (services/scan.py `attach`).
                "duplicate_of": None,
                # Closed is shown, not hidden: the report and materials the
                # user bought stay readable, and the mark says why to stop.
                "closed": (
                    {"at": v.closed_at.isoformat(), "reason": v.closed_reason}
                    if v.closed_at
                    else None
                ),
                # Old, from a source that cannot be asked. Not closed — the
                # list folds it with the closed ones, and says why.
                "stale": None if v.closed_at else liveness.stale_reason(v, now),
                "hidden": v.hidden_at is not None,
                "published": v.published.isoformat() if v.published else None,
            }
        )

    return {
        "has_resume": resume is not None,
        "resume_name": resume.filename if resume else None,
        "profile": resume.profile if resume else None,
        "search_profile": resume.search_profile if resume else None,
        "resume_history": await _resume_history(session, user.id),
        "found": found,
        "statuses": STATUSES,
        "apply_steps": steps_as_list(),
        "spend": {
            "found": len(found),
            "scored": scored,
            "with_artifacts": with_artifacts,
            # Shares of the daily and weekly quota, never money, so the screen
            # can show the ceiling before it is hit.
            "quota": quota(user),
        },
        "user": {
            "email": user.email,
            "name": user.name,
            # Shows the dashboard in the sidebar; the API checks it again.
            "is_admin": user.is_admin,
            "consent_at": user.consent_at,
            "telegram_name": user.telegram_name if user.telegram_id is not None else None,
        },
    }
