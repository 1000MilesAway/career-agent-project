"""Application materials: tailored resume, cover letter, interview prep.

Built on top of an existing evaluation, never by re-running the whole pipeline.
The report is the input for all three; running `run_all` again would buy the
same judgement a second time (+0.17 RUB per click), which is the trap the demo
hit and fixed.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.llm import LLM, Ledger
from core.pipeline import cover_letter, interview_prep, tailor_resume
from core.profiles import steps_as_list

from ..core.deps import ResumeDep, SessionDep, UserDep
from ..models import Artifact, Evaluation, Vacancy
from ..services.spend import charge, ensure_can_spend

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

Kind = Literal["tailor", "letter", "interview"]

# Which core function builds which artifact.
BUILDERS = {
    "tailor": tailor_resume,
    "letter": cover_letter,
    "interview": interview_prep,
}


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    kind: str
    content: dict[str, Any]
    cost_rub: float
    cached: bool = False

    @classmethod
    def of(cls, a: Artifact, cached: bool = False) -> ArtifactResponse:
        return cls(
            id=a.id,
            kind=a.kind,
            content=a.content,
            cost_rub=float(a.cost_rub),
            cached=cached,
        )


@router.get("/kinds")
async def kinds() -> list[dict]:
    """What can be bought, with the price, so the UI states it before charging.

    The key is `step`, matching what /api/state returns and what core.profiles
    calls it. Two names for one thing across two endpoints is how a screen ends
    up reading `undefined` from the wrong one.
    """
    return steps_as_list()


@router.post("/{vacancy_id}/{kind}", response_model=ArtifactResponse)
async def create(
    vacancy_id: uuid.UUID,
    kind: Kind,
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> ArtifactResponse:
    """Buy one document for one vacancy.

    Requires an evaluation: without a report there is nothing to build from,
    and silently running the evaluation here would charge for a step the user
    did not ask for.
    """
    result = await session.execute(
        select(Evaluation)
        .join(Vacancy)
        .where(
            Evaluation.vacancy_id == vacancy_id,
            Evaluation.resume_id == resume.id,
            Vacancy.user_id == user.id,
        )
        .options(selectinload(Evaluation.artifacts), selectinload(Evaluation.vacancy))
    )
    evaluation = result.scalar_one_or_none()
    if evaluation is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Сначала оцените вакансию — материалы строятся на отчёте",
        )

    for existing in evaluation.artifacts:
        if existing.kind == kind:
            return ArtifactResponse.of(existing, cached=True)

    vacancy_text = evaluation.vacancy.raw_text
    if not vacancy_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Текст вакансии не сохранён — материалы построить не из чего",
        )

    ensure_can_spend(user)

    ledger = Ledger()
    llm = LLM(ledger=ledger)
    content = await run_in_threadpool(
        BUILDERS[kind], llm, resume.profile, evaluation.report, vacancy_text
    )

    if not content:
        # An empty result is a failure, not a document. Storing it would make
        # the row count as "has materials" and hide the failure behind a label.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Модель вернула пустой результат — ничего не сохранено",
        )

    artifact = Artifact(
        evaluation_id=evaluation.id,
        kind=kind,
        content=content,
        cost_rub=round(ledger.rub, 4),
    )
    session.add(artifact)
    await charge(session, user, ledger)
    await session.commit()
    await session.refresh(artifact)
    return ArtifactResponse.of(artifact)


@router.get("/{vacancy_id}", response_model=list[ArtifactResponse])
async def listing(
    vacancy_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> list[ArtifactResponse]:
    """Everything already bought for this vacancy. Free to re-read."""
    result = await session.execute(
        select(Artifact)
        .join(Evaluation)
        .join(Vacancy, Evaluation.vacancy_id == Vacancy.id)
        .where(
            Evaluation.vacancy_id == vacancy_id,
            Evaluation.resume_id == resume.id,
            Vacancy.user_id == user.id,
        )
    )
    return [ArtifactResponse.of(a, cached=True) for a in result.scalars()]
