"""Collect vacancies, look at what was found, triage, shortlist, track."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.sources import geekjob

from ..core.deps import OptionalResumeDep, ResumeDep, SessionDep, UserDep
from ..models import Vacancy
from ..services import added, liveness, scan, sources
from ..services import triage as triage_service
from ..services.tracker import STATUSES, set_status
from ..services.triage import TriageRequest

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
    accredited: bool | None = None
    stage: str
    screen_reason: str | None
    triage_verdict: str | None
    is_saved: bool
    score: float | None
    verdict: str | None
    status: str | None
    closed_at: datetime | None = None
    closed_reason: str | None = None
    checked_at: datetime | None = None
    hidden_at: datetime | None = None
    added_at: datetime | None = None

    @classmethod
    def of(cls, v: Vacancy, switches: sources.Switches | None = None) -> VacancyResponse:
        evaluation = v.evaluations[0] if v.evaluations else None
        company = switches.company(v.company) if switches else v.company
        return cls(
            id=v.id,
            source=v.source,
            external_id=v.external_id,
            url=v.url,
            title=v.title,
            company=company,
            qualification=v.qualification,
            salary_text=v.salary_text,
            remote=v.remote,
            accredited=v.accredited,
            stage=v.stage,
            screen_reason=v.screen_reason,
            triage_verdict=v.triage_verdict,
            is_saved=v.is_saved,
            score=evaluation.score if evaluation else None,
            verdict=evaluation.verdict if evaluation else None,
            status=v.application.status if v.application else None,
            closed_at=v.closed_at,
            closed_reason=v.closed_reason,
            checked_at=v.checked_at,
            hidden_at=v.hidden_at,
            added_at=v.added_at,
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
    switches = await sources.load(session)
    return [VacancyResponse.of(v, switches) for v in result.scalars()]


@router.get("/screened")
async def screened(
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
    limit: int = Query(scan.SCREENED_SHOWN, ge=1, le=scan.SCREENED_SHOWN),
) -> list[dict[str, Any]]:
    """What the free filters drop from the shared pool, each with its reason.

    The discard log. Dropped postings no longer get a row per user, so the
    reason is recomputed here — free and deterministic, the same one the
    scan had. Free: no model, no network.
    """
    try:
        return await scan.explain_screened(session, user.id, resume.search_profile, limit)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


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
    body: TriageRequest | None = None,
) -> dict[str, Any]:
    """The cheap stage, holding the request open until the model answers.

    The cabinet does not call this any more: it queues a `triage` job
    (POST /api/jobs), which the worker runs with the same `triage.run`.
    Kept for the API, where waiting on the answer is the point.
    """
    ids = body.ids if body is not None else None
    result = await triage_service.run(session, user, resume, ids)
    await session.commit()
    return result


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
    # Nor what the user brought by hand: «Мои вакансии» is not a search result.
    result = await session.execute(
        select(Vacancy).where(
            Vacancy.user_id == user.id,
            Vacancy.is_saved.is_(False),
            Vacancy.added_at.is_(None),
        )
    )
    rows = list(result.scalars())
    for row in rows:
        await session.delete(row)
    await session.commit()
    return {"deleted": len(rows)}


class PreviewRequest(BaseModel):
    link: str = Field(max_length=2048)


class PreviewResponse(BaseModel):
    posting_id: uuid.UUID
    title: str
    company: str | None
    salary_text: str | None
    location: str | None
    url: str | None
    closed_reason: str | None
    # True: the user already has it in «Избранное»; adding changes nothing.
    saved: bool


@router.post("/add/preview", response_model=PreviewResponse)
async def preview(body: PreviewRequest, session: SessionDep, user: UserDep) -> PreviewResponse:
    """Read a link and show what it is, before it becomes the user's. Free.

    Only links to sources we read are accepted (core/sources/user_added.py);
    the page is read here, so a link that cannot be read says so now — not
    after paying for an evaluation of nothing.
    """
    try:
        posting = await added.preview(session, body.link)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    row = await added.owned_row(session, user, posting)
    await session.commit()
    switches = await sources.load(session)
    return PreviewResponse(
        posting_id=posting.id,
        title=posting.title,
        company=switches.company(posting.company) or None,
        salary_text=posting.salary_text or None,
        location=posting.location or None,
        url=posting.url,
        closed_reason=posting.closed_reason,
        saved=bool(row and row.is_saved),
    )


class AddRequest(BaseModel):
    # The posting a preview returned: the page is not read a second time.
    posting_id: uuid.UUID


class AddResponse(BaseModel):
    vacancy: VacancyResponse
    # False: the user already had it — found by a scan or added before.
    created: bool


@router.post("/add", response_model=AddResponse)
async def add(
    body: AddRequest, session: SessionDep, user: UserDep, resume: OptionalResumeDep
) -> AddResponse:
    """Put a previewed vacancy into «Избранное». Free: no model is called."""
    try:
        vacancy, created = await added.add(session, user, resume, body.posting_id)
    except LookupError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена"
        ) from e
    await session.commit()
    return AddResponse(vacancy=VacancyResponse.of(await _owned(session, user, vacancy.id)),
                       created=created)


@router.delete("/{vacancy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(vacancy_id: uuid.UUID, session: SessionDep, user: UserDep) -> None:
    """Delete a vacancy the user added, with its report and materials.

    Only an added one: a scan's row is folded with "Скрыть" and comes back
    with one click, and deleting it would only make the next scan bring it
    back. An added one has no scan to bring it back, so it is the user's to
    delete — the cabinet asks first.
    """
    vacancy = await _owned(session, user, vacancy_id)
    if vacancy.added_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Удалить можно только добавленную вами вакансию; найденную — скрыть.",
        )
    await added.remove(session, vacancy)
    await session.commit()


@router.get("/statuses")
async def statuses() -> dict[str, dict]:
    """The tracker's vocabulary, for the UI to render."""
    return STATUSES


@router.get("/{vacancy_id}", response_model=VacancyResponse)
async def detail(vacancy_id: uuid.UUID, session: SessionDep, user: UserDep) -> VacancyResponse:
    return VacancyResponse.of(await _owned(session, user, vacancy_id))
