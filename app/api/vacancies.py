"""Collect vacancies, look at what was found, triage, shortlist, track."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.llm import LLM, Ledger
from core.sources import geekjob
from core.triage import build_archetypes, run_triage

from ..core.deps import ResumeDep, SessionDep, UserDep
from ..models import Vacancy
from ..services import liveness, scan
from ..services.spend import charge, ensure_can_spend
from ..services.tracker import STATUSES, set_status

router = APIRouter(prefix="/api/vacancies", tags=["vacancies"])


class VacancyResponse(BaseModel):
    id: uuid.UUID
    source: str
    external_id: str
    url: str | None
    title: str
    company: str | None
    qualification: str | None
    salary_text: str | None
    remote: bool | None
    stage: str
    screen_reason: str | None
    triage_verdict: str | None
    is_saved: bool
    score: float | None
    verdict: str | None
    status: str | None
    duplicate_of_id: uuid.UUID | None = None
    closed_at: datetime | None = None
    closed_reason: str | None = None
    checked_at: datetime | None = None
    hidden_at: datetime | None = None

    @classmethod
    def of(cls, v: Vacancy) -> VacancyResponse:
        evaluation = v.evaluations[0] if v.evaluations else None
        return cls(
            id=v.id,
            source=v.source,
            external_id=v.external_id,
            url=v.url,
            title=v.title,
            company=v.company,
            qualification=v.qualification,
            salary_text=v.salary_text,
            remote=v.remote,
            stage=v.stage,
            screen_reason=v.screen_reason,
            triage_verdict=v.triage_verdict,
            is_saved=v.is_saved,
            score=evaluation.score if evaluation else None,
            verdict=evaluation.verdict if evaluation else None,
            status=v.application.status if v.application else None,
            duplicate_of_id=v.duplicate_of_id,
            closed_at=v.closed_at,
            closed_reason=v.closed_reason,
            checked_at=v.checked_at,
            hidden_at=v.hidden_at,
        )


@router.post("/scan")
async def start_scan(
    body: scan.ScanRequest,
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> dict[str, Any]:
    """Collect and screen, holding the request open until done.

    The cabinet does not call this any more: a scan of everything takes up to
    a minute, so it queues a `scan` job instead (POST /api/jobs) and the
    worker runs the same `scan.run_scan`. Kept for the API and for debugging
    one board, where waiting on the answer is the point.
    """
    try:
        result = await scan.run_scan(session, user, resume, body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except (geekjob.MarkupChanged, httpx.HTTPError) as e:
        # Said out loud: an empty result here would read as "nothing new
        # on this board today", which is exactly what a broken parser looks like.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{body.source} не прочитан: {e}",
        ) from e
    await session.commit()
    return result


@router.get("", response_model=list[VacancyResponse])
async def listing(
    session: SessionDep,
    user: UserDep,
    stage: str | None = Query(None, description="collected | screened_out | shortlist | evaluated"),
    saved: bool | None = Query(None),
) -> list[VacancyResponse]:
    """What the scan found.

    Dropped postings are returned too, carrying their reason. They are not
    hidden: the user has to be able to tell a working filter from an
    over-eager one, which is why career-ops keeps discard.log at all.
    """
    query = (
        select(Vacancy)
        .where(Vacancy.user_id == user.id)
        .options(selectinload(Vacancy.evaluations), selectinload(Vacancy.application))
        .order_by(Vacancy.created_at.desc())
    )
    if stage:
        query = query.where(Vacancy.stage == stage)
    if saved is not None:
        query = query.where(Vacancy.is_saved.is_(saved))

    result = await session.execute(query)
    return [VacancyResponse.of(v) for v in result.scalars()]


async def _owned(session: SessionDep, user: UserDep, vacancy_id: uuid.UUID) -> Vacancy:
    """One vacancy belonging to this user, or 404.

    Scoped by user_id, not just by id: an id from another account must read as
    "not found" rather than as someone else's vacancy.
    """
    result = await session.execute(
        select(Vacancy)
        .where(Vacancy.id == vacancy_id, Vacancy.user_id == user.id)
        .options(selectinload(Vacancy.evaluations), selectinload(Vacancy.application))
    )
    vacancy = result.scalar_one_or_none()
    if vacancy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена")
    return vacancy


@router.post("/recheck")
async def recheck(session: SessionDep, user: UserDep) -> dict[str, int]:
    """Ask the sources whether saved and shortlisted vacancies are still open.

    Free: one page request per vacancy, no model. Bounded per call (see
    services/liveness.py), oldest check first, so pressing it again continues
    where the last press stopped.
    """
    counts = await liveness.recheck(session, user.id)
    await session.commit()
    return counts


@router.post("/{vacancy_id}/check", response_model=VacancyResponse)
async def check_one(vacancy_id: uuid.UUID, session: SessionDep, user: UserDep) -> VacancyResponse:
    """Is this one vacancy still open? Free. Unknown sources answer unknown."""
    vacancy = await _owned(session, user, vacancy_id)
    await liveness.refresh(vacancy)
    await session.commit()
    return VacancyResponse.of(vacancy)


@router.post("/triage")
async def triage(
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> dict[str, Any]:
    """The cheap stage: one model call over the whole batch.

    Judges listing data only — no page is fetched. Nothing is deleted: every
    verdict is written back with its reason, and postings the model failed to
    judge go to `missing` and stay visible. Silently dropping an unjudged
    vacancy is the worst bug this pipeline can have, so it is not done.
    """
    # Only the unjudged. A verdict survives in `triage_verdict` after the
    # batch runs, but a "worth"/"maybe" row stays in SHORTLIST — selecting by
    # stage alone sent the whole surviving list back through a paid call every
    # time the button was pressed. Evaluations and artifacts both refuse to be
    # bought twice; this one did not.
    result = await session.execute(
        select(Vacancy).where(
            Vacancy.user_id == user.id,
            Vacancy.stage == scan.SHORTLIST,
            Vacancy.triage_verdict.is_(None),
            # A vacancy known to be closed is not worth a paid verdict. It
            # stays in the list, marked, and is left unjudged.
            Vacancy.closed_at.is_(None),
            # Hidden by the user: "not interested" is already a verdict, and a
            # paid one on top of it buys nothing.
            Vacancy.hidden_at.is_(None),
        )
    )
    rows = list(result.scalars())
    if not rows:
        return {"judged": 0, "missing": [], "note": "Нечего сортировать — всё уже отсеяно"}

    ensure_can_spend(user)

    ledger = Ledger()
    llm = LLM(ledger=ledger)

    archetypes = resume.archetypes
    if not archetypes:
        archetypes = await run_in_threadpool(build_archetypes, llm, resume.profile)
        resume.archetypes = archetypes

    digests = [
        {
            "id": str(v.id),
            "title": v.title,
            "company": v.company,
            "qualification": v.qualification,
            "salary_text": v.salary_text,
            "divisions": v.divisions,
            "skills": v.skills,
        }
        for v in rows
    ]
    outcome = await run_in_threadpool(run_triage, llm, archetypes, digests)

    by_id = {str(v.id): v for v in rows}
    for vid, verdict in outcome.verdicts.items():
        row = by_id.get(vid)
        if row is None:
            continue
        row.triage_verdict = verdict["bucket"]
        row.screen_reason = verdict.get("reason") or None
        if verdict["bucket"] == "skip":
            row.stage = scan.TRIAGED_OUT

    await charge(session, user, ledger)
    await session.commit()

    return {
        "judged": len(outcome.verdicts),
        "counts": outcome.counts(),
        # Postings the model returned no verdict for. Shown, never backfilled
        # as "skip": an unjudged vacancy is not a rejected one.
        "missing": outcome.missing,
        # Ids the model invented, which are not ours. Reported so a broken
        # prompt looks broken instead of looking like a short answer.
        "unknown": outcome.unknown,
        "cost_rub": round(ledger.rub, 4),
    }


@router.post("/{vacancy_id}/save", response_model=VacancyResponse)
async def save(vacancy_id: uuid.UUID, session: SessionDep, user: UserDep) -> VacancyResponse:
    """Put a vacancy on the shortlist. Only a person crosses this line."""
    vacancy = await _owned(session, user, vacancy_id)
    if not vacancy.is_saved:
        vacancy.is_saved = True
        vacancy.saved_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(vacancy)
    return VacancyResponse.of(vacancy)


@router.delete("/{vacancy_id}/save", response_model=VacancyResponse)
async def unsave(vacancy_id: uuid.UUID, session: SessionDep, user: UserDep) -> VacancyResponse:
    vacancy = await _owned(session, user, vacancy_id)
    if vacancy.is_saved:
        vacancy.is_saved = False
        vacancy.saved_at = None
        await session.commit()
        await session.refresh(vacancy)
    return VacancyResponse.of(vacancy)


@router.post("/{vacancy_id}/hide", response_model=VacancyResponse)
async def hide(vacancy_id: uuid.UUID, session: SessionDep, user: UserDep) -> VacancyResponse:
    """"Не интересно": fold the row out of the list. Nothing is deleted.

    The row keeps whatever was bought for it and comes back with one click
    from the "скрытые" toggle — a drop the user cannot see or undo is the
    silent discard the pipeline forbids, even when the user made it.
    """
    vacancy = await _owned(session, user, vacancy_id)
    if vacancy.hidden_at is None:
        vacancy.hidden_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(vacancy)
    return VacancyResponse.of(vacancy)


@router.delete("/{vacancy_id}/hide", response_model=VacancyResponse)
async def unhide(vacancy_id: uuid.UUID, session: SessionDep, user: UserDep) -> VacancyResponse:
    vacancy = await _owned(session, user, vacancy_id)
    if vacancy.hidden_at is not None:
        vacancy.hidden_at = None
        await session.commit()
        await session.refresh(vacancy)
    return VacancyResponse.of(vacancy)


class StatusRequest(BaseModel):
    status: str
    note: str = ""


@router.put("/{vacancy_id}/status", response_model=VacancyResponse)
async def update_status(
    vacancy_id: uuid.UUID,
    body: StatusRequest,
    session: SessionDep,
    user: UserDep,
) -> VacancyResponse:
    """Move a vacancy along the tracker. Transitions are appended, never lost."""
    vacancy = await _owned(session, user, vacancy_id)
    try:
        set_status(vacancy, body.status, body.note)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    await session.commit()
    await session.refresh(vacancy)
    return VacancyResponse.of(vacancy)


@router.post("/clear")
async def clear(session: SessionDep, user: UserDep) -> dict[str, int]:
    """Drop the search results — but never the shortlist.

    "Очистить" lives on the search page and means "this batch was not useful".
    A vacancy the user deliberately shortlisted is the opposite of that, and it
    carries reports and materials they paid for. Dropping those from a button
    labelled "clear the list of results" would be data loss the label never
    warned about.
    """
    result = await session.execute(
        select(Vacancy).where(Vacancy.user_id == user.id, Vacancy.is_saved.is_(False))
    )
    rows = list(result.scalars())
    for row in rows:
        await session.delete(row)
    await session.commit()
    return {"deleted": len(rows)}


@router.get("/statuses")
async def statuses() -> dict[str, dict]:
    """The tracker's vocabulary, for the UI to render."""
    return STATUSES


@router.get("/{vacancy_id}", response_model=VacancyResponse)
async def detail(vacancy_id: uuid.UUID, session: SessionDep, user: UserDep) -> VacancyResponse:
    return VacancyResponse.of(await _owned(session, user, vacancy_id))
