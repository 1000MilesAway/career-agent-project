"""Upload a CV, read the parsed profile, confirm the search profile."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select, update

from core.defaults import starter_profile
from core.llm import LLM, Ledger
from core.msdoc import DocError
from core.pipeline import build_search_profile, parse_resume, read_document

from ..core.deps import ResumeDep, SessionDep, UserDep
from ..models import Resume
from ..services.spend import charge, ensure_can_spend

router = APIRouter(prefix="/api/resumes", tags=["resumes"])

# What read_document can actually turn into text.
ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx", ".txt", ".md"}
MAX_BYTES = 10 * 1024 * 1024


class ResumeResponse(BaseModel):
    id: uuid.UUID
    filename: str
    profile: dict[str, Any]
    search_profile: dict[str, Any] | None
    archetypes: dict[str, Any] | None


def _as_response(resume: Resume) -> ResumeResponse:
    return ResumeResponse(
        id=resume.id,
        filename=resume.filename,
        profile=resume.profile,
        search_profile=resume.search_profile,
        archetypes=resume.archetypes,
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
            # Word 95, password-protected, RTF under a .doc name: the user can
            # fix each by resaving, so they get the reason instead of a 500.
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
    await session.execute(
        update(Resume)
        .where(Resume.user_id == user.id, Resume.is_active.is_(True))
        .values(is_active=False)
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


@router.get("", response_model=list[ResumeResponse])
async def history(session: SessionDep, user: UserDep) -> list[ResumeResponse]:
    """Every CV uploaded, newest first — old ones still back stored evaluations."""
    result = await session.execute(
        select(Resume).where(Resume.user_id == user.id).order_by(Resume.created_at.desc())
    )
    return [_as_response(r) for r in result.scalars()]
