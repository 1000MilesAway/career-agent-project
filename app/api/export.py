"""Downloading a resume as PDF, DOCX or Markdown, and previewing it as HTML.

Free: every download renders JSON that was already paid for — the parsed
profile on `resumes.profile`, or the tailored CV on `artifacts.content`. No
step here calls the model, so `ensure_can_spend` is deliberately absent; a
download that charged would make re-reading a stored document cost money, the
same trap `/api/artifacts` avoids by returning the cached row.

Rendering happens in `core/export.py`, which knows nothing about the database.
This module only decides what the user is allowed to download and how the file
reaches the browser.
"""

from __future__ import annotations

import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy import select

from core.contacts import extract_contacts
from core.export import (
    MEDIA_TYPES,
    Document,
    ascii_filename,
    filename,
    profile_document,
    render,
    tailored_resume_document,
)
from core.resume_styles import DEFAULT_STYLE, STYLES

from ..core.deps import ResumeDep, SessionDep, UserDep
from ..models import Artifact, Evaluation, Vacancy

router = APIRouter(prefix="/api/export", tags=["export"])

# "html" is the preview: the exact page WeasyPrint turns into the PDF, so the
# cabinet cannot show one look and hand over another.
Format = Literal["pdf", "docx", "md", "html"]

# Validated here so an unknown id is a 422 naming the allowed values, not a
# KeyError deep in the renderer.
StyleId = Literal[tuple(STYLES)]  # type: ignore[valid-type]
StyleQuery = Query(DEFAULT_STYLE, description="Оформление: id из /api/export/styles")


def _file_response(doc: Document, fmt: Format, stem: str, style: str) -> Response:
    """Send the bytes as an attachment.

    Both `filename` and `filename*` are set: the plain one is ASCII for old
    clients, the starred one carries the real, possibly Cyrillic name per
    RFC 5987. Sending only the plain one turns "Иванов.pdf" into mojibake in
    the save dialog.
    """
    content = render(doc, fmt, style)
    if fmt == "html":
        # Shown in the cabinet, not saved: no attachment header. The cabinet
        # puts it into a sandboxed iframe, and all user text is escaped.
        return Response(content=content, media_type=MEDIA_TYPES[fmt])
    name = filename(stem, fmt)
    ascii_name = ascii_filename(name)
    return Response(
        content=content,
        media_type=MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(name, safe='')}"
            )
        },
    )


async def _render(doc: Document, fmt: Format, stem: str, style: str) -> Response:
    """Render off the event loop.

    WeasyPrint is synchronous and CPU-bound for a second or so on a long CV;
    run inline it would block every other request on the worker for that time.
    """
    try:
        return await run_in_threadpool(_file_response, doc, fmt, stem, style)
    except (ImportError, OSError) as exc:
        # WeasyPrint loads Pango/Cairo through cffi and raises OSError — NOT
        # ImportError — when the system libraries are absent; the import
        # itself succeeds. Catching only ImportError produced a 500, verified
        # against a host without libpangoft2. Both are caught because the
        # import does fail outright if the wheel is missing entirely.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Выгрузка в PDF недоступна на этом сервере — не установлены системные библиотеки",
        ) from exc


@router.get("/styles")
async def list_styles(_user: UserDep) -> list[dict[str, str]]:
    """The presets the constructor offers, default first."""
    ordered = [STYLES[DEFAULT_STYLE]] + [s for s in STYLES.values() if s.id != DEFAULT_STYLE]
    return [{"id": s.id, "name": s.name, "description": s.description} for s in ordered]


@router.get("/resume.{fmt}")
async def export_profile(
    fmt: Format, resume: ResumeDep, style: StyleId = StyleQuery
) -> Response:
    """The CV the user uploaded, as parsed into a profile."""
    # Contacts are read from the uploaded text, not asked of the model: see
    # core/contacts.py. Free, and it covers resumes parsed before they existed.
    doc = profile_document(resume.profile, contacts=extract_contacts(resume.raw_text))
    return await _render(doc, fmt, doc.title, style)


@router.get("/vacancy/{vacancy_id}/tailor.{fmt}")
async def export_tailored(
    vacancy_id: uuid.UUID,
    fmt: Format,
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
    style: StyleId = StyleQuery,
) -> Response:
    """The tailored CV bought for one vacancy.

    404 when it has not been bought: rendering an empty document would hand
    the user a blank file and read as a broken download rather than as a step
    they have not taken yet.
    """
    result = await session.execute(
        select(Artifact, Vacancy)
        .join(Evaluation, Artifact.evaluation_id == Evaluation.id)
        .join(Vacancy, Evaluation.vacancy_id == Vacancy.id)
        .where(
            Evaluation.vacancy_id == vacancy_id,
            Evaluation.resume_id == resume.id,
            Vacancy.user_id == user.id,
            Artifact.kind == "tailor",
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Резюме под эту вакансию ещё не готово — закажите его в избранном",
        )

    artifact, vacancy = row
    doc = tailored_resume_document(
        artifact.content,
        profile=resume.profile,
        vacancy_title=vacancy.title,
        contacts=extract_contacts(resume.raw_text),
    )
    # The company in the file name is what the user sees in their downloads
    # folder three days later, when "resume.pdf" tells them nothing.
    stem = " — ".join(p for p in (doc.title, vacancy.company or vacancy.title) if p)
    return await _render(doc, fmt, stem, style)
