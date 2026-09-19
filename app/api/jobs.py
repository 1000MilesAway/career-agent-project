"""Queued work, for steps too slow to hold an HTTP request open.

An evaluation takes ~13 s and a full package ~54 s. The SPA creates a job,
polls `GET /api/jobs/{id}` every couple of seconds and draws progress, which
is what docs/03-architecture.md specifies instead of WebSockets.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from ..core.deps import ResumeDep, SessionDep, UserDep
from ..models import Job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

JobKind = Literal["evaluate", "tailor", "letter", "interview", "scan"]


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
    """
    job = Job(
        user_id=user.id,
        kind=body.kind,
        payload={**body.payload, "resume_id": str(resume.id)},
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
async def listing(session: SessionDep, user: UserDep) -> list[JobResponse]:
    result = await session.execute(
        select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(50)
    )
    return [JobResponse.of(j) for j in result.scalars()]
