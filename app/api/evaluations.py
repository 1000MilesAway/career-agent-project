"""The paid stage: a full evaluation of one vacancy against the active CV."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.llm import LLM, Ledger
from core.pipeline import evaluate, load_vacancy

from ..core.deps import ResumeDep, SessionDep, UserDep
from ..models import Evaluation, Vacancy
from ..services import liveness, scan
from ..services.spend import charge, ensure_can_spend, ledger_totals

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


class EvaluationResponse(BaseModel):
    id: uuid.UUID
    vacancy_id: uuid.UUID
    score: float | None
    verdict: str | None
    report: dict[str, Any]
    cached: bool = False

    @classmethod
    def of(cls, e: Evaluation, cached: bool = False) -> EvaluationResponse:
        return cls(
            id=e.id,
            vacancy_id=e.vacancy_id,
            score=e.score,
            verdict=e.verdict,
            report=e.report,
            cached=cached,
        )


async def _vacancy_text(vacancy: Vacancy) -> str:
    """The posting text the model reads.

    Telegram rows carry their text in the row, because t.me has no page to
    fetch: `load_vacancy` on a t.me URL fails. Habr rows can be fetched on
    demand and then keep the text, so a re-evaluation costs no second request.
    """
    if vacancy.raw_text:
        return vacancy.raw_text

    if not vacancy.url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="У вакансии нет ни текста, ни ссылки — оценить нечего",
        )

    try:
        text, _meta = await run_in_threadpool(load_vacancy, vacancy.url)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось получить текст вакансии: {e}",
        ) from e

    # On the shared posting: the next user to evaluate it reads it from here.
    vacancy.posting.raw_text = text
    return text


@router.post("/{vacancy_id}", response_model=EvaluationResponse)
async def create(
    vacancy_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> EvaluationResponse:
    """Evaluate one vacancy. Costs ~0.17 RUB.

    An evaluation the user already bought for this (vacancy, resume) pair is
    returned as-is rather than bought again — the demo's `cached: true`. Paying
    twice for the same judgement is the bug the artifact buttons were built to
    avoid.
    """
    result = await session.execute(
        select(Vacancy)
        .where(Vacancy.id == vacancy_id, Vacancy.user_id == user.id)
        .options(selectinload(Vacancy.evaluations))
    )
    vacancy = result.scalar_one_or_none()
    if vacancy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена")

    for existing in vacancy.evaluations:
        if existing.resume_id == resume.id:
            return EvaluationResponse.of(existing, cached=True)

    # Checked before the call, never after: refusing afterwards would mean the
    # money is already spent and the user is told they cannot have it.
    ensure_can_spend(user)

    # Free, and before the paid call for the same reason as the budget check.
    # An archived Habr page still answers 200 with a stub, so without this
    # the model would be paid to score "Вакансия в архиве".
    if liveness.needs_check(vacancy):
        await liveness.refresh(vacancy)
    if vacancy.closed_at is not None:
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Вакансия закрыта: {vacancy.closed_reason}. Оценка не покупалась.",
        )

    text = await _vacancy_text(vacancy)

    ledger = Ledger()
    llm = LLM(ledger=ledger)
    report = await run_in_threadpool(evaluate, llm, resume.profile, text)

    totals = ledger_totals(ledger)
    evaluation = Evaluation(
        vacancy_id=vacancy.id,
        resume_id=resume.id,
        score=report.get("score"),
        verdict=report.get("verdict"),
        report=report,
        **totals,
    )
    session.add(evaluation)
    vacancy.stage = scan.EVALUATED
    await charge(session, user, ledger)
    await session.commit()
    await session.refresh(evaluation)
    return EvaluationResponse.of(evaluation)


@router.get("/{vacancy_id}", response_model=EvaluationResponse)
async def read(
    vacancy_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> EvaluationResponse:
    """The stored evaluation. Free — the user already paid for it."""
    result = await session.execute(
        select(Evaluation)
        .join(Vacancy)
        .where(
            Evaluation.vacancy_id == vacancy_id,
            Evaluation.resume_id == resume.id,
            Vacancy.user_id == user.id,
        )
    )
    evaluation = result.scalar_one_or_none()
    if evaluation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Оценки пока нет")
    return EvaluationResponse.of(evaluation, cached=True)
