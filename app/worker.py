"""The job worker: poll the table, run the step, write the result back.

    uv run python -m app.worker

A separate process from the API, because an LLM call takes tens of seconds and
must not occupy a request handler. Run one; two would need the claim below to
be the only thing keeping them apart, and it is — but nothing else here has
been tested under concurrency.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.llm import LLM, Ledger
from core.pipeline import (
    build_search_profile,
    cover_letter,
    evaluate,
    interview_prep,
    tailor_resume,
)

from .core.db import SessionLocal
from .models import Artifact, Evaluation, Job, Resume, User, Vacancy
from .services import scan
from .services.spend import charge, ledger_totals, remaining_rub

log = logging.getLogger("worker")

POLL_SECONDS = 2.0
MAX_ATTEMPTS = 3

BUILDERS = {
    "tailor": tailor_resume,
    "letter": cover_letter,
    "interview": interview_prep,
}


async def claim(session: AsyncSession) -> Job | None:
    """Take the oldest queued job, or None.

    `SKIP LOCKED` is what makes the claim safe: two workers asking at the same
    moment get different rows instead of both running the same job and billing
    the user twice.
    """
    result = await session.execute(
        select(Job)
        .where(Job.status == "queued")
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return None

    job.status = "running"
    job.attempts += 1
    job.started_at = datetime.now(UTC)
    await session.commit()
    return job


async def _run_evaluate(session: AsyncSession, job: Job, user: User, resume: Resume) -> dict:
    vacancy_id = uuid.UUID(job.payload["vacancy_id"])
    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None or vacancy.user_id != user.id:
        raise ValueError("вакансия не найдена")

    existing = await session.execute(
        select(Evaluation).where(
            Evaluation.vacancy_id == vacancy.id, Evaluation.resume_id == resume.id
        )
    )
    stored = existing.scalar_one_or_none()
    if stored is not None:
        # Already bought. Re-running would charge for the same judgement.
        return {"evaluation_id": str(stored.id), "cached": True}

    if not vacancy.raw_text:
        raise ValueError("у вакансии нет текста")

    ledger = Ledger()
    llm = LLM(ledger=ledger)
    report = await asyncio.to_thread(evaluate, llm, resume.profile, vacancy.raw_text)

    evaluation = Evaluation(
        vacancy_id=vacancy.id,
        resume_id=resume.id,
        score=report.get("score"),
        verdict=report.get("verdict"),
        report=report,
        **ledger_totals(ledger),
    )
    session.add(evaluation)
    vacancy.stage = scan.EVALUATED
    await charge(session, user, ledger)
    await session.flush()
    return {"evaluation_id": str(evaluation.id), "score": report.get("score")}


async def _run_artifact(
    session: AsyncSession, job: Job, user: User, resume: Resume, kind: str
) -> dict:
    vacancy_id = uuid.UUID(job.payload["vacancy_id"])
    result = await session.execute(
        select(Evaluation).where(
            Evaluation.vacancy_id == vacancy_id, Evaluation.resume_id == resume.id
        )
    )
    evaluation = result.scalar_one_or_none()
    if evaluation is None:
        raise ValueError("нет оценки — материалы строятся на отчёте")

    existing = await session.execute(
        select(Artifact).where(Artifact.evaluation_id == evaluation.id, Artifact.kind == kind)
    )
    stored = existing.scalar_one_or_none()
    if stored is not None:
        return {"artifact_id": str(stored.id), "cached": True}

    vacancy = await session.get(Vacancy, vacancy_id)
    if vacancy is None or not vacancy.raw_text:
        raise ValueError("текст вакансии не сохранён")

    ledger = Ledger()
    llm = LLM(ledger=ledger)
    content = await asyncio.to_thread(
        BUILDERS[kind], llm, resume.profile, evaluation.report, vacancy.raw_text
    )
    if not content:
        # Empty is a failure, not a document: storing it would make the row
        # count as "has materials" and hide the failure behind a label.
        raise ValueError("модель вернула пустой результат")

    artifact = Artifact(
        evaluation_id=evaluation.id,
        kind=kind,
        content=content,
        cost_rub=round(ledger.rub, 4),
    )
    session.add(artifact)
    await charge(session, user, ledger)
    await session.flush()
    return {"artifact_id": str(artifact.id)}


async def run_job(session: AsyncSession, job: Job) -> dict:
    user = await session.get(User, job.user_id)
    if user is None:
        raise ValueError("пользователь удалён")

    resume = await session.get(Resume, uuid.UUID(job.payload["resume_id"]))
    if resume is None or resume.user_id != user.id:
        raise ValueError("резюме не найдено")

    # The same budget the API enforces, checked here too: a job queued while
    # the account still had money must not run after it ran out. Raised as a
    # plain error rather than an HTTPException — nobody is holding a request.
    left = remaining_rub(user)
    if left is not None and left <= 0 and job.kind != "scan":
        raise ValueError(
            f"исчерпан лимит расхода, потрачено {float(user.spent_rub):.2f} ₽"
        )

    if job.kind == "evaluate":
        return await _run_evaluate(session, job, user, resume)
    if job.kind in BUILDERS:
        return await _run_artifact(session, job, user, resume, job.kind)
    if job.kind == "scan":
        return await _run_scan(session, job, user, resume)
    raise ValueError(f"неизвестный тип задачи: {job.kind}")


async def _run_scan(
    session: AsyncSession, job: Job, user: User, resume: Resume
) -> dict:
    """Collect and screen Habr, deriving the search profile if there is none.

    A CV uploaded before this behaviour existed has no search profile, and a
    scan on an empty one keeps the whole catalogue: 125 unscreened rows on
    2026-09-19, none carrying a reason. Rather than refusing and leaving the
    user stuck, the profile is derived from the stored CV here and saved, so
    the next scan reuses it instead of paying again.
    """
    if not resume.search_profile:
        ledger = Ledger()
        llm = LLM(ledger=ledger)
        resume.search_profile = await asyncio.to_thread(
            build_search_profile, llm, resume.profile
        )
        await charge(session, user, ledger)
        await session.commit()
        log.info("job %s: derived a search profile for resume %s", job.id, resume.id)

    return await scan.collect_habr(session, user.id, resume.search_profile)


async def tick() -> bool:
    """One pass. Returns whether a job was picked up."""
    async with SessionLocal() as session:
        job = await claim(session)
        if job is None:
            return False

        try:
            result = await run_job(session, job)
        except Exception as e:
            await session.rollback()
            # Re-fetch: the rollback detached whatever the failed run touched.
            job = await session.get(Job, job.id)
            if job is None:
                return True
            job.error = f"{type(e).__name__}: {e}"[:2000]
            # Retry only while attempts remain. A job that fails three times is
            # failing for a reason a fourth run will not fix.
            job.status = "queued" if job.attempts < MAX_ATTEMPTS else "failed"
            job.finished_at = datetime.now(UTC) if job.status == "failed" else None
            await session.commit()
            log.warning("job %s failed (%s/%s): %s", job.id, job.attempts, MAX_ATTEMPTS, e)
            return True

        job.status = "done"
        job.result = result
        job.error = None
        job.finished_at = datetime.now(UTC)
        await session.commit()
        log.info("job %s done", job.id)
        return True


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("worker started, polling every %.1fs", POLL_SECONDS)

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Finish the job in flight instead of dying mid-call: the money is
        # already spent by then, and the result would be lost.
        loop.add_signal_handler(sig, stopping.set)

    while not stopping.is_set():
        try:
            busy = await tick()
        except Exception:
            log.exception("worker loop error")
            busy = False
        if not busy:
            try:
                await asyncio.wait_for(stopping.wait(), timeout=POLL_SECONDS)
            except TimeoutError:
                pass

    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
