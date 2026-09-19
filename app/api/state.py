"""One call that answers "what should the screen show right now".

The demo's screen was built against a single `/api/state`, and keeping that
shape is deliberate: the alternative was five round-trips on every page load
and a rewrite of the client for no user-visible gain.

Free — nothing here calls the model.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.profiles import steps_as_list

from ..core.deps import SessionDep, UserDep
from ..models import Evaluation, Resume, Vacancy
from ..services.spend import remaining_rub
from ..services.tracker import STATUSES

router = APIRouter(prefix="/api", tags=["state"])


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
            selectinload(Vacancy.evaluations).selectinload(Evaluation.artifacts),
            selectinload(Vacancy.application),
        )
        .order_by(Vacancy.created_at.desc())
    )
    vacancies = list(vacancies_row.scalars())

    found = []
    spent_total = 0.0
    scored = 0
    with_artifacts = 0

    for v in vacancies:
        evaluation = next(
            (e for e in v.evaluations if resume and e.resume_id == resume.id),
            v.evaluations[0] if v.evaluations else None,
        )
        artifacts: dict[str, Any] = {}
        if evaluation:
            spent_total += float(evaluation.cost_rub)
            scored += 1
            for a in evaluation.artifacts:
                artifacts[a.kind] = a.content
                spent_total += float(a.cost_rub)
        if artifacts:
            with_artifacts += 1

        found.append(
            {
                "id": str(v.id),
                "url": v.url,
                "title": v.title,
                "company": v.company,
                "qualification": v.qualification,
                "salary_text": v.salary_text,
                "remote": v.remote,
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
                "rub": float(evaluation.cost_rub) if evaluation else None,
            }
        )

    return {
        "has_resume": resume is not None,
        "resume_name": resume.filename if resume else None,
        "profile": resume.profile if resume else None,
        "search_profile": resume.search_profile if resume else None,
        "found": found,
        "statuses": STATUSES,
        "apply_steps": steps_as_list(),
        "spend": {
            "found": len(found),
            "scored": scored,
            "with_artifacts": with_artifacts,
            "rub": round(spent_total, 3),
            "free_to_reopen": scored + with_artifacts,
            # The ceiling, so the screen can show it before it is hit.
            "limit_rub": remaining_rub(user),
            "spent_total_rub": float(user.spent_rub),
        },
        "user": {"email": user.email, "consent_at": user.consent_at},
    }
