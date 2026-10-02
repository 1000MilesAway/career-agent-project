"""Upload a CV, read the parsed profile, confirm the search profile."""

from __future__ import annotations

import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update

from core.defaults import starter_profile
from core.llm import LLM, Ledger
from core.msdoc import DocError
from core.pipeline import build_search_profile, parse_resume, read_document

from ..core.deps import ResumeDep, SessionDep, UserDep
from ..models import Resume
from ..services import chat as chat_service
from ..services import revisions
from ..services.spend import charge, ensure_can_spend

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

# What read_document can actually turn into text.
ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx", ".rtf", ".txt", ".md"}
MAX_BYTES = 10 * 1024 * 1024


class ResumeResponse(BaseModel):
    id: uuid.UUID
    filename: str
    profile: dict[str, Any]
    search_profile: dict[str, Any] | None
    archetypes: dict[str, Any] | None
    revision: int
    parent_id: uuid.UUID | None
    is_active: bool
    created_at: datetime


def _as_response(resume: Resume) -> ResumeResponse:
    return ResumeResponse(
        id=resume.id,
        filename=resume.filename,
        profile=resume.profile,
        search_profile=resume.search_profile,
        archetypes=resume.archetypes,
        revision=resume.revision,
        parent_id=resume.parent_id,
        is_active=resume.is_active,
        created_at=resume.created_at,
    )


@router.post("", response_model=ResumeResponse, status_code=status.HTTP_201_CREATED)
async def upload(
    session: SessionDep,
    user: UserDep,
    file: UploadFile = File(...),
) -> ResumeResponse:
    """Parse a CV and store the profile.

    Parsing costs money (~0.05 RUB), so the result is stored rather than
    recomputed on every request: parsing a CV costs money.
    """
    # A CV is personal data: none is stored, or sent to the model, before the
    # user agreed. The cabinet asks first; this is what holds without it.
    if user.consent_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Сначала дайте согласие на обработку персональных данных",
        )
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Поддерживаются {', '.join(sorted(ALLOWED_SUFFIXES))}",
        )

    payload = await file.read()
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Файл больше 10 МБ",
        )
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл пустой")

    # read_document works on paths (pymupdf/python-docx both want one), so the
    # upload is spooled to disk and removed straight after.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(payload)
        tmp.flush()
        try:
            text = await run_in_threadpool(read_document, Path(tmp.name))
        except DocError as exc:
            # Word 95 or password-protected: the user can fix either by
            # resaving, so they get the reason instead of a 500.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Не удалось прочитать .doc: {exc}",
            ) from None

    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Из файла не извлёкся текст — возможно, это скан",
        )

    ensure_can_spend(user)

    ledger = Ledger()
    llm = LLM(ledger=ledger)
    profile = await run_in_threadpool(parse_resume, llm, text)

    # Built here rather than proposed for confirmation: a CV with no search
    # profile behind it left the scan with nothing to filter on, and the empty
    # profile that resulted kept the whole Habr catalogue. Both calls are on
    # the same upload, so the user pays once and lands in a state that works.
    # The profile stays editable afterwards — see docs/decisions.md.
    search_profile = await run_in_threadpool(build_search_profile, llm, profile)

    # A new CV replaces the old active one rather than deleting it: evaluations
    # point at the resume they judged against, and dropping it would take
    # paid-for reports with it.
    replaced = await session.execute(
        update(Resume)
        .where(Resume.user_id == user.id, Resume.is_active.is_(True))
        .values(is_active=False)
    )
    if replaced.rowcount:
        await chat_service.note_resume_changed(
            session,
            user.id,
            f"Загружено новое резюме «{file.filename or 'resume'}». Правки, принятые "
            "в разговоре выше, относились к прежнему резюме и в новом их нет.",
        )

    resume = Resume(
        user_id=user.id,
        filename=file.filename or "resume",
        raw_text=text,
        profile=profile,
        search_profile=search_profile,
        is_active=True,
    )
    session.add(resume)
    await charge(session, user, ledger)
    await session.commit()
    await session.refresh(resume)
    return _as_response(resume)


@router.get("/active", response_model=ResumeResponse)
async def active(resume: ResumeDep) -> ResumeResponse:
    return _as_response(resume)


def _clean_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _clean_list(items: list[str] | None) -> list[str]:
    """Trimmed, without blanks and repeats: a textarea line per item leaves both."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        item = item.strip()
        if item and item.casefold() not in seen:
            seen.add(item.casefold())
            out.append(item)
    return out


# Generous caps: they exist so a request cannot store megabytes, not to
# second-guess a long CV.
_Line = Field(default=None, max_length=5000)
_Items = Field(default=None, max_length=300)


class ExperienceEdit(BaseModel):
    # Extra keys pass through: the parser may add one this form does not know,
    # and a manual edit must not strip it.
    model_config = ConfigDict(extra="allow")

    company: str | None = _Line
    role: str | None = _Line
    period: str | None = _Line
    location: str | None = _Line
    description: str | None = _Line
    achievements: list[str] | None = _Items

    @field_validator("company", "role", "period", "location", "description")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return _clean_text(value)

    @field_validator("achievements")
    @classmethod
    def _list(cls, value: list[str] | None) -> list[str]:
        return _clean_list(value)


class ProfileEdit(BaseModel):
    """The parsed profile as the user corrected it by hand.

    Keys left out of the request keep their stored value: the form edits what
    it shows, and `preferences` or anything else it does not show survives.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = _Line
    target_role: str | None = _Line
    seniority: Literal["intern", "junior", "middle", "senior", "lead"] | None = None
    years_experience: float | None = Field(default=None, ge=0, le=70)
    field: str | None = _Line
    summary: str | None = Field(default=None, max_length=20000)
    directions: list[str] | None = _Items
    skills: list[str] | None = _Items
    education: list[str] | None = _Items
    languages: list[str] | None = _Items
    experience: list[ExperienceEdit] | None = _Items

    @field_validator("name", "target_role", "field", "summary")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        return _clean_text(value)

    @field_validator("directions", "skills", "education", "languages")
    @classmethod
    def _list(cls, value: list[str] | None) -> list[str]:
        return _clean_list(value)

    @field_validator("experience")
    @classmethod
    def _jobs(cls, value: list[ExperienceEdit] | None) -> list[ExperienceEdit]:
        # A block the user cleared entirely is a removed job, not an empty one:
        # an empty entry would reach the exported file as a stray heading.
        return [
            job
            for job in value or []
            if any(v for v in job.model_dump(exclude_none=True).values())
        ]


@router.put("/active/profile", response_model=ResumeResponse)
async def edit_profile(
    body: ProfileEdit,
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> ResumeResponse:
    """Save the profile the user edited by hand. Free — no model call.

    The user typed the change, so the save is the confirmation, as with a
    rollback. It lands as a new revision rather than an edit in place: reports
    bought against the old profile must keep matching what they read, and a new
    resume_id is what lets a vacancy be scored again (docs/decisions.md,
    2026-09-22, variant B).
    """
    edited = body.model_dump(mode="json", exclude_unset=True)
    for job in edited.get("experience") or []:
        # exclude_unset keeps extras but also any explicit nulls; drop the
        # nulls so a cleared field reads the same as one the parser left out.
        for key in [k for k, v in job.items() if v is None]:
            del job[key]
    years = edited.get("years_experience")
    if isinstance(years, float) and years.is_integer():
        edited["years_experience"] = int(years)

    profile = {**(resume.profile or {}), **edited}
    if profile == resume.profile:
        # Nothing changed: a revision here would only push the reports bought
        # against this version into "прежнее резюме" for no reason.
        return _as_response(resume)

    revision = await revisions.new_revision(session, resume, profile=profile)
    await chat_service.note_resume_changed(
        session,
        user.id,
        f"Резюме отредактировано вручную (версия {resume.revision + 1}). "
        "Актуальное содержимое — в профиле, а не в репликах выше.",
    )
    await session.commit()
    await session.refresh(revision)
    return _as_response(revision)


class SearchProfileProposal(BaseModel):
    """What the model suggests, for the user to accept or edit.

    Nothing here is stored. The confirmation layer is a product rule, not a
    prompt instruction: the agent proposes, the user accepts.
    """

    current: dict[str, Any]
    proposed: dict[str, Any]


@router.post("/search-profile/propose", response_model=SearchProfileProposal)
async def propose_search_profile(
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> SearchProfileProposal:
    """Suggest queries and stop-words from the CV. Stores nothing."""
    ensure_can_spend(user)

    ledger = Ledger()
    llm = LLM(ledger=ledger)
    proposed = await run_in_threadpool(build_search_profile, llm, resume.profile)
    await charge(session, user, ledger)
    await session.commit()

    return SearchProfileProposal(
        current=resume.search_profile or starter_profile().as_dict(),
        proposed=proposed,
    )


@router.put("/search-profile", response_model=ResumeResponse)
async def accept_search_profile(
    body: dict[str, Any],
    session: SessionDep,
    resume: ResumeDep,
) -> ResumeResponse:
    """Store the search profile the user accepted.

    A PUT of the whole object, not a merge of the proposal: what the user sees
    in the diff is exactly what gets saved, including any edits they made to it.
    """
    resume.search_profile = body
    await session.commit()
    await session.refresh(resume)
    return _as_response(resume)


@router.post("/{resume_id}/restore", response_model=ResumeResponse)
async def restore(
    resume_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> ResumeResponse:
    """Go back to an earlier version. Free — nothing here calls the model.

    The click is the confirmation: the user picked the version by hand. What
    it does is copy that version forward as a new revision, so the rollback
    can itself be rolled back.
    """
    result = await session.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id)
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Версия не найдена")
    if target.id == resume.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Эта версия и так текущая"
        )

    restored = await revisions.restore(session, resume, target)
    await chat_service.note_resume_changed(
        session,
        user.id,
        f"Резюме возвращено к версии {target.revision} от "
        f"{target.created_at:%d.%m.%Y %H:%M} UTC. Правки, сделанные после неё, "
        "в текущем резюме отсутствуют.",
    )
    await session.commit()
    await session.refresh(restored)
    return _as_response(restored)


@router.get("", response_model=list[ResumeResponse])
async def history(session: SessionDep, user: UserDep) -> list[ResumeResponse]:
    """Every CV uploaded, newest first — old ones still back stored evaluations."""
    result = await session.execute(
        select(Resume).where(Resume.user_id == user.id).order_by(Resume.created_at.desc())
    )
    return [_as_response(r) for r in result.scalars()]
