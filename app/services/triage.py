"""The cheap stage: batched model calls over the user's unjudged shortlist.

Shared by POST /api/vacancies/triage and the jobs worker, so the button and
the API cannot drift on what a triage means. The cabinet queues it as a job:
held open inside a request it kept the cabinet's navigation waiting for the
model (docs/decisions.md, 2026-10-03).

Judges listing data only — no page is fetched. Nothing is deleted: every
verdict is written back with its reason, and postings the model failed to
judge go to `missing` and stay visible. Silently dropping an unjudged vacancy
is the worst bug this pipeline can have, so it is not done.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.llm import LLM, Ledger
from core.triage import build_archetypes, run_triage

from ..models import Posting, Resume, User, Vacancy
from . import scan
from .spend import charge, ensure_can_spend

NOTHING_TO_DO = "Нечего сортировать — всё уже отсеяно"


class TriageRequest(BaseModel):
    # The rows the cabinet still shows under the user's conditions. A row the
    # conditions fold away is a free-stage reject in all but name, and a paid
    # verdict on it is the money the pipeline order exists to save. Omitted —
    # every unjudged row.
    ids: list[uuid.UUID] | None = None


async def run(
    session: AsyncSession,
    user: User,
    resume: Resume,
    ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """Judge the unjudged rows, or only `ids` among them. Does not commit.

    `ids` are the rows the cabinet still shows under the user's conditions. A
    row the conditions fold away is a free-stage reject in all but name, and
    a paid verdict on it is the money the pipeline order exists to save.
    """
    # Only the unjudged. A verdict survives in `triage_verdict` after the
    # batch runs, but a "worth"/"maybe" row stays in SHORTLIST — selecting by
    # stage alone sent the whole surviving list back through a paid call every
    # time the button was pressed. Evaluations and artifacts both refuse to be
    # bought twice; this one did not.
    query = select(Vacancy).join(Vacancy.posting).where(
        Vacancy.user_id == user.id,
        Vacancy.stage == scan.SHORTLIST,
        Vacancy.triage_verdict.is_(None),
        # A vacancy known to be closed is not worth a paid verdict. It
        # stays in the list, marked, and is left unjudged.
        Posting.closed_at.is_(None),
        # Hidden by the user: "not interested" is already a verdict, and a
        # paid one on top of it buys nothing.
        Vacancy.hidden_at.is_(None),
        # Added by hand: the user asked about it by name, and the evaluation
        # is the click they came for. Triage is the scan list's sieve.
        Vacancy.added_at.is_(None),
    )
    if ids is not None:
        query = query.where(Vacancy.id.in_(ids))
    rows = list((await session.execute(query)).scalars())
    if not rows:
        return {"judged": 0, "missing": [], "note": NOTHING_TO_DO}

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

    return {
        "judged": len(outcome.verdicts),
        "counts": outcome.counts(),
        # Postings the model returned no verdict for. Shown, never backfilled
        # as "skip": an unjudged vacancy is not a rejected one.
        "missing": outcome.missing,
        # Ids the model invented, which are not ours. Reported so a broken
        # prompt looks broken instead of looking like a short answer.
        "unknown": outcome.unknown,
    }
