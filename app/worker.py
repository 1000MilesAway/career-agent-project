"""The job worker: poll the table, run the step, write the result back.

    uv run python -m app.worker

A separate process from the API, because an LLM call takes tens of seconds and
must not occupy a request handler. Alongside the queue it runs the liveness
sweep (services/liveness.py): free, HTTP only, and on its own task so a
minute of page requests never delays a job the user is waiting for. Run one; two would need the claim below to
be the only thing keeping them apart, and it is — but nothing else here has
been tested under concurrency.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.llm import LLM, Ledger
from core.pipeline import cover_letter, evaluate, interview_prep, tailor_resume

from .core.db import SessionLocal
from .models import Artifact, Evaluation, Job, Resume, User, Vacancy
from .services import liveness, scan
from .services.spend import charge, ledger_totals, remaining_rub

log = logging.getLogger("worker")

POLL_SECONDS = 2.0
# Pause between liveness passes. One pass is a bounded batch, oldest check
# first; at this pace a few hundred live rows are revisited about twice a day.
SWEEP_SECONDS = 300.0
MAX_ATTEMPTS = 3
# Kinds that fail on the first error; see `tick`.
NOT_RETRIED = frozenset({"scan"})


class PaidFailure(Exception):
    """A failure that happened AFTER the model was billed. Never retried.

    The rollback that follows a failed job also rolls back `charge`, so a
    retry buys the same tokens again and records none of it: three empty
    replies in a row meant three paid calls and a spend total still reading
    zero. Retrying is only safe while nothing has been bought yet.
    """

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
        # count as "has materials" and hide the failure behind a label. The
        # tokens are already spent, so this must not come back for a second
        # and third attempt.
        raise PaidFailure("модель вернула пустой результат")

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
    """Collect and screen with the settings the scan form sent.

    The same `scan.run_scan` as POST /api/vacancies/scan: the cabinet queues
    the scan here so a minute of network does not hold the page, and two
    copies of the dispatch would drift. The profile is derived first when the
    CV has none (see `scan.ensure_search_profile`).
    """
    params = scan.ScanRequest.model_validate(job.payload)
    return await scan.run_scan(session, user, resume, params)


def _error_text(error: Exception) -> str:
    """What the user reads under a failed job.

    An HTTPException is the budget refusal shared with the API (402 from
    `ensure_can_spend`); its detail is already a sentence for the user.
    """
    if isinstance(error, HTTPException):
        return str(error.detail)[:2000]
    return f"{type(error).__name__}: {error}"[:2000]


async def reap_orphans() -> int:
    """Fail the jobs a previous worker left `running`.

    One worker runs, so at startup nothing can legitimately be running. A job
    in that state was cut off by a kill or a crash, and without this it stays
    "running" forever: the cabinet keeps showing a scan in progress and
    refuses to queue another. Failed, not requeued — a paid step may already
    have bought its tokens, and a rerun would buy them twice.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            update(Job)
            .where(Job.status == "running")
            .values(
                status="failed",
                error="прервано перезапуском сервиса — запустите ещё раз",
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()
        return result.rowcount or 0


async def tick() -> bool:
    """One pass. Returns whether a job was picked up."""
    async with SessionLocal() as session:
        job = await claim(session)
        if job is None:
            return False
        # Read before the run: the rollback below expires `job`, and touching
        # `job.id` afterwards is a lazy load outside the greenlet — it raised
        # MissingGreenlet, the failure was never recorded, and the job sat in
        # "running" for good.
        job_id = job.id

        try:
            result = await run_job(session, job)
        except Exception as e:
            await session.rollback()
            # Re-fetch: the rollback detached whatever the failed run touched.
            job = await session.get(Job, job_id)
            if job is None:
                return True
            job.error = _error_text(e)
            # Retry only while attempts remain, and never when the model was
            # already billed: the rollback above undid the charge too, so a
            # retry would buy the same tokens again off the books.
            #
            # A scan is not retried either: it is free, but the user is
            # watching it, and a second minute-long pass over the same boards
            # after a deterministic failure (no profile, a changed parser)
            # only delays the error they need to see.
            retryable = not isinstance(e, PaidFailure) and job.kind not in NOT_RETRIED
            job.status = "queued" if retryable and job.attempts < MAX_ATTEMPTS else "failed"
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


async def sweep_once() -> dict[str, int]:
    async with SessionLocal() as session:
        counts = await liveness.sweep(session)
        await session.commit()
        return counts


async def sweep_loop(stopping: asyncio.Event) -> None:
    """Recheck found vacancies in the background until the worker stops.

    Failures are logged and the loop goes on: a board that is down now is the
    usual case, not a reason to stop checking the others later.
    """
    while not stopping.is_set():
        try:
            counts = await sweep_once()
            if counts["checked"]:
                log.info("liveness sweep: %s", counts)
        except Exception:
            log.exception("liveness sweep error")
        try:
            await asyncio.wait_for(stopping.wait(), timeout=SWEEP_SECONDS)
        except TimeoutError:
            pass


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("worker started, polling every %.1fs", POLL_SECONDS)

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Finish the job in flight instead of dying mid-call: the money is
        # already spent by then, and the result would be lost.
        loop.add_signal_handler(sig, stopping.set)

    reaped = await reap_orphans()
    if reaped:
        log.warning("failed %s job(s) left running by a previous worker", reaped)

    sweeper = asyncio.create_task(sweep_loop(stopping))

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

    await sweeper
    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
