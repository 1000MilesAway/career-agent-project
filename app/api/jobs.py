"""Queued work, for steps too slow to hold an HTTP request open.

An evaluation takes ~13 s and a full package ~54 s. The SPA creates a job,
polls `GET /api/jobs/{id}` every couple of seconds and draws progress, which
is what docs/03-architecture.md specifies instead of WebSockets.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from ..core.deps import ResumeDep, SessionDep, UserDep
from ..models import Job
from ..services.scan import ScanRequest
from ..services.triage import TriageRequest

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

JobKind = Literal["evaluate", "tailor", "letter", "interview", "scan", "triage"]
# What the cabinet may read back. "refresh" is queued by the worker after a
# scan answered from a stale pool, never by a user.
ListedKind = Literal[JobKind, "refresh"]
# Kinds a user runs one at a time: a second press, or a second tab, gets the
# job already under way instead of walking the boards or paying twice.
ONE_AT_A_TIME = ("scan", "triage")
ACTIVE = ("queued", "running")


class JobRequest(BaseModel):
    kind: JobKind
    payload: dict[str, Any] = {}


class JobResponse(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    attempts: int
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None

    @classmethod
    def of(cls, job: Job) -> JobResponse:
        return cls(
            id=job.id,
            kind=job.kind,
            status=job.status,
            attempts=job.attempts,
            result=job.result,
            error=job.error,
            created_at=job.created_at,
            finished_at=job.finished_at,
        )


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def enqueue(
    body: JobRequest,
    session: SessionDep,
    user: UserDep,
    resume: ResumeDep,
) -> JobResponse:
    """Queue a job. The worker picks it up; this returns immediately.

    `resume_id` is pinned at enqueue time rather than resolved by the worker:
    the user may upload a new CV while the job waits, and the report must say
    which resume it actually judged.

    A scan needs a search profile, but does not refuse without one: the worker
    derives it from the stored CV first (see `_run_scan`). What must never
    happen is a scan on an EMPTY profile, which filters nothing — that is
    guarded in `collect_habr`.

    A scan is what the cabinet's search button queues: it takes up to a
    minute, and holding the request open for it held the whole page. Triage
    is queued for the same reason — a model call held the cabinet's
    navigation while it ran.
    """
    payload = body.payload
    # Checked here, not in the worker: a bad form must answer 422 now, not a
    # failed job a few seconds later. Stored normalised, so the worker reads
    # exactly what was validated.
    form = {"scan": ScanRequest, "triage": TriageRequest}.get(body.kind)
    if form is not None:
        try:
            payload = form.model_validate(body.payload).model_dump(mode="json")
        except ValidationError as e:
            raise RequestValidationError(e.errors()) from e

    if body.kind in ONE_AT_A_TIME:
        active = await session.execute(
            select(Job)
            .where(
                Job.user_id == user.id,
                Job.kind == body.kind,
                Job.status.in_(ACTIVE),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        running = active.scalar_one_or_none()
        if running is not None:
            return JobResponse.of(running)

    job = Job(
        user_id=user.id,
        kind=body.kind,
        payload={**payload, "resume_id": str(resume.id)},
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return JobResponse.of(job)


@router.get("/{job_id}", response_model=JobResponse)
async def read(job_id: uuid.UUID, session: SessionDep, user: UserDep) -> JobResponse:
    result = await session.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    return JobResponse.of(job)


@router.get("", response_model=list[JobResponse])
async def listing(
    session: SessionDep,
    user: UserDep,
    kind: ListedKind | None = Query(None),
    limit: int = Query(50, ge=1, le=50),
) -> list[JobResponse]:
    """Newest first. `?kind=scan&limit=1` is what the cabinet polls while a
    scan runs: one small row every couple of seconds, not the whole state."""
    query = select(Job).where(Job.user_id == user.id)
    if kind is not None:
        query = query.where(Job.kind == kind)
    result = await session.execute(query.order_by(Job.created_at.desc()).limit(limit))
    return [JobResponse.of(j) for j in result.scalars()]
