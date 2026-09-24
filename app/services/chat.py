"""The chat's tools, wired to the database.

`core/chat.py` owns the loop and knows nothing about storage; this is the half
that reads and writes. The split is the rule from CLAUDE.md — nothing in
`core/` imports `app/` — and it is what lets the loop be tested without
Postgres.

The safety property this module carries: **a proposing tool never writes.**
`handle` dispatches reads straight to the database and turns every PROPOSE
call into a row in `proposals` with status `pending`. Accepting one is a
separate request the user makes. The refusal to write is structural, not a
promise in a prompt: there is no branch here that applies a change.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core import tools

from ..models import Evaluation, Proposal, Resume, User, Vacancy
from .spend import remaining_rub
from .tracker import STATUSES

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"

# How much of the conversation is replayed to the model. The prefix cache makes
# a long history cheap, but not free, and a chat that never forgets eventually
# costs more per reply than the reply is worth.
HISTORY_LIMIT = 40

# Vacancy text handed to the model is trimmed: a posting is context for the
# conversation, not the subject of it, and a 20 KB wall of text would crowd out
# the profile it has to be compared against.
VACANCY_TEXT_LIMIT = 4000


def _vacancy_digest(vacancy: Vacancy, evaluation: Evaluation | None) -> dict[str, Any]:
    """What the model sees about one posting.

    The report is included when it has already been bought, because re-reading
    a stored judgement is free and the chat's whole job is to talk about it.
    """
    out: dict[str, Any] = {
        "id": str(vacancy.id),
        "title": vacancy.title,
        "company": vacancy.company,
        "qualification": vacancy.qualification,
        "salary_text": vacancy.salary_text,
        "remote": vacancy.remote,
        "url": vacancy.url,
        "stage": vacancy.stage,
        "saved": vacancy.is_saved,
        "status": vacancy.application.status if vacancy.application else None,
    }
    if vacancy.screen_reason:
        out["screen_reason"] = vacancy.screen_reason
    if vacancy.raw_text:
        out["text"] = vacancy.raw_text[:VACANCY_TEXT_LIMIT]
    if evaluation is not None:
        out["report"] = evaluation.report
        out["score"] = evaluation.score
        out["verdict"] = evaluation.verdict
    return out


def split_requirements(report: dict[str, Any] | None) -> dict[str, list]:
    """Requirements of a bought report, in three buckets.

    Taken from career-ops' `jd-skill-gap.mjs`, which sorts requirements into
    "already named", "demonstrated by the CV but not named" and "no trace" —
    and which never writes anything, by design. The middle bucket is the useful
    one: often the user does not need to add experience, only to surface what
    the CV already says badly.

    No model call: this reads a report that was already paid for.
    """
    buckets: dict[str, list] = {"covered": [], "partial": [], "missing": []}
    for item in (report or {}).get("requirements") or []:
        if not isinstance(item, dict):
            continue
        match = str(item.get("match") or "").lower()
        entry = {
            "text": item.get("text"),
            "weight": item.get("weight") or item.get("importance"),
            "evidence": item.get("evidence"),
            "advice": item.get("gap_advice"),
        }
        if match == "full":
            buckets["covered"].append(entry)
        elif match == "partial":
            buckets["partial"].append(entry)
        else:
            buckets["missing"].append(entry)
    return buckets


async def _active_resume(session: AsyncSession, user: User) -> Resume | None:
    result = await session.execute(
        select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def _owned_vacancy(
    session: AsyncSession, user: User, vacancy_id: str
) -> Vacancy | None:
    try:
        parsed = uuid.UUID(str(vacancy_id))
    except (ValueError, AttributeError, TypeError):
        return None
    result = await session.execute(
        select(Vacancy)
        .where(Vacancy.id == parsed, Vacancy.user_id == user.id)
        .options(selectinload(Vacancy.evaluations), selectinload(Vacancy.application))
    )
    return result.scalar_one_or_none()


def _evaluation_for(vacancy: Vacancy, resume: Resume | None) -> Evaluation | None:
    """The report judged against the CV in force now.

    A report bought against an older CV is deliberately NOT returned as if it
    were current: `state.py` does that and it quietly shows a score computed
    against a profile that no longer exists.
    """
    if resume is None:
        return None
    return next((e for e in vacancy.evaluations if e.resume_id == resume.id), None)


def enabled_tools(has_resume: bool) -> tuple[tools.Tool, ...]:
    """Which tools the model is offered.

    Without a CV there is no profile to read or patch, and offering the tool
    anyway produces a confident call that fails — the model then apologises
    instead of saying the useful thing, which is "upload a CV first".
    """
    if has_resume:
        return tools.TOOLS
    return tuple(
        t
        for t in tools.TOOLS
        if t.name not in {"get_profile", "propose_profile_patch", "skill_gap"}
    )


class ToolRunner:
    """Executes one tool call against this user's data.

    Reads answer immediately. Proposals are recorded and returned as pending —
    there is no code path here that applies one.
    """

    def __init__(self, session: AsyncSession, user: User, message_id: uuid.UUID | None = None):
        self.session = session
        self.user = user
        self.message_id = message_id
        # Proposals created during this turn, so the caller can show them.
        self.proposals: list[Proposal] = []

    async def __call__(self, name: str, arguments: dict) -> dict:
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return {"error": f"инструмент {name} не поддерживается"}
        return await handler(arguments)

    # --- reads -----------------------------------------------------------

    async def _get_profile(self, _arguments: dict) -> dict:
        resume = await _active_resume(self.session, self.user)
        if resume is None:
            return {"error": "резюме не загружено"}
        return {"filename": resume.filename, "profile": resume.profile}

    async def _get_spend(self, _arguments: dict) -> dict:
        return {
            "spent_rub": float(self.user.spent_rub),
            "remaining_rub": remaining_rub(self.user),
        }

    async def _list_vacancies(self, arguments: dict) -> dict:
        query = select(Vacancy).where(Vacancy.user_id == self.user.id)
        if arguments.get("only_saved"):
            query = query.where(Vacancy.is_saved.is_(True))
        result = await self.session.execute(
            query.options(selectinload(Vacancy.evaluations)).order_by(
                Vacancy.created_at.desc()
            )
        )
        rows = list(result.scalars())

        needle = str(arguments.get("query") or "").strip().casefold()
        if needle:
            rows = [
                v
                for v in rows
                if needle in (v.title or "").casefold()
                or needle in (v.company or "").casefold()
            ]
        if arguments.get("only_evaluated"):
            rows = [v for v in rows if v.evaluations]

        # Trimmed: the model needs enough to pick an id, not the whole board.
        return {
            "count": len(rows),
            "vacancies": [
                {
                    "id": str(v.id),
                    "title": v.title,
                    "company": v.company,
                    "score": v.evaluations[0].score if v.evaluations else None,
                    "saved": v.is_saved,
                }
                for v in rows[:30]
            ],
        }

    async def _get_vacancy(self, arguments: dict) -> dict:
        vacancy = await _owned_vacancy(self.session, self.user, arguments.get("vacancy_id"))
        if vacancy is None:
            return {"error": "вакансия не найдена"}
        resume = await _active_resume(self.session, self.user)
        return _vacancy_digest(vacancy, _evaluation_for(vacancy, resume))

    async def _skill_gap(self, arguments: dict) -> dict:
        vacancy = await _owned_vacancy(self.session, self.user, arguments.get("vacancy_id"))
        if vacancy is None:
            return {"error": "вакансия не найдена"}
        resume = await _active_resume(self.session, self.user)
        evaluation = _evaluation_for(vacancy, resume)
        if evaluation is None:
            return {
                "error": (
                    "по этой вакансии ещё нет оценки против текущего резюме — "
                    "её нужно заказать кнопкой на экране"
                )
            }
        return {
            "vacancy": vacancy.title,
            "score": evaluation.score,
            **split_requirements(evaluation.report),
        }

    # --- proposals: prepared, never applied ------------------------------

    def _propose(
        self,
        kind: str,
        arguments: dict,
        current: dict | None = None,
        proposed: dict | None = None,
        rationale: str = "",
    ) -> dict:
        proposal = Proposal(
            user_id=self.user.id,
            message_id=self.message_id,
            kind=kind,
            status=PENDING,
            arguments=arguments,
            current=current,
            proposed=proposed,
            rationale=rationale or None,
        )
        self.session.add(proposal)
        self.proposals.append(proposal)
        # The model is told plainly that nothing happened yet, so it does not
        # report the change as done.
        return {
            "status": "pending_confirmation",
            "note": "Изменение подготовлено и ждёт подтверждения пользователя.",
        }

    async def _propose_profile_patch(self, arguments: dict) -> dict:
        resume = await _active_resume(self.session, self.user)
        if resume is None:
            return {"error": "резюме не загружено"}

        op = str(arguments.get("op") or "").strip()
        value = str(arguments.get("value") or "").strip()
        if not value:
            return {"error": "нечего добавлять: пустое значение"}
        if op not in {"add_skill", "add_achievement", "set_field"}:
            return {"error": f"неизвестная операция: {op}"}

        return self._propose(
            kind="profile_patch",
            arguments=arguments,
            current={"profile": resume.profile},
            proposed=None,  # computed on accept, against the profile of that moment
            rationale=str(arguments.get("rationale") or ""),
        )

    async def _set_vacancy_status(self, arguments: dict) -> dict:
        vacancy = await _owned_vacancy(self.session, self.user, arguments.get("vacancy_id"))
        if vacancy is None:
            return {"error": "вакансия не найдена"}
        status = str(arguments.get("status") or "").strip().lower()
        if status not in STATUSES:
            return {
                "error": f"неизвестный статус: {status}",
                "allowed": sorted(STATUSES),
            }
        return self._propose(
            kind="vacancy_status",
            arguments={"vacancy_id": str(vacancy.id), "status": status},
            current={"status": vacancy.application.status if vacancy.application else "none"},
            proposed={"status": status},
            rationale=f"{vacancy.title} → {STATUSES[status]['title']}",
        )

    async def _save_vacancy(self, arguments: dict) -> dict:
        vacancy = await _owned_vacancy(self.session, self.user, arguments.get("vacancy_id"))
        if vacancy is None:
            return {"error": "вакансия не найдена"}
        saved = bool(arguments.get("saved", True))
        return self._propose(
            kind="vacancy_saved",
            arguments={"vacancy_id": str(vacancy.id), "saved": saved},
            current={"saved": vacancy.is_saved},
            proposed={"saved": saved},
            rationale=f"{vacancy.title}: {'в избранное' if saved else 'убрать из избранного'}",
        )


def _employer_key(name: str) -> str:
    """A company name reduced to what a person would call it in a chat.

    The CV says "Berezka VPN (свой проект, self-hosted)" or "iiko, Компания
    Айко"; a user asking the assistant says "Berezka VPN" or "iiko".
    """
    name = re.sub(r"\(.*?\)", " ", name.casefold())
    return " ".join(re.findall(r"\w+", name))


def _find_employer(experience: list[dict], company: str) -> dict | None:
    """The experience entry `company` refers to, or None.

    Exact match first, then the name without the parenthetical note, then the
    name as the leading words of the entry's. Exact-only matching sent an
    achievement for "Berezka VPN" into a new blank job next to the real
    "Berezka VPN (свой проект, self-hosted)" — see docs/decisions.md.
    A loose match counts only when it is unambiguous: attaching an
    achievement to the wrong employer is worse than a visible new entry.
    """
    if not company:
        return None
    for entry in experience:
        if str(entry.get("company", "")).casefold() == company.casefold():
            return entry

    wanted = _employer_key(company)
    if not wanted:
        return None
    for matches in (
        lambda key: key == wanted,
        lambda key: key.startswith(wanted + " "),
    ):
        found = [e for e in experience if matches(_employer_key(str(e.get("company", ""))))]
        if len(found) == 1:
            return found[0]
    return None


def apply_profile_patch(profile: dict[str, Any], arguments: dict) -> dict[str, Any]:
    """The accepted patch, applied to a copy of the profile.

    Additive by design, following career-ops' `expand` mode ("Additive Only —
    under pain of never deleting or overwriting"): a chat message is a reason
    to add something, never a reason to drop what the CV already said.

    Every value written carries where it came from, so a later tailoring step
    can tell a fact off the CV from a sentence the user typed into a chat.
    """
    out = dict(profile or {})
    op = str(arguments.get("op") or "")
    value = str(arguments.get("value") or "").strip()
    stamp = datetime.now(UTC).date().isoformat()

    if op == "add_skill":
        skills = list(out.get("skills") or [])
        if not any(str(s).casefold() == value.casefold() for s in skills):
            skills.append(value)
        out["skills"] = skills
        provenance = dict(out.get("provenance") or {})
        entries = list(provenance.get("skills") or [])
        entries.append({"value": value, "source": "user-stated", "at": stamp})
        provenance["skills"] = entries
        out["provenance"] = provenance
        return out

    if op == "add_achievement":
        company = str(arguments.get("company") or "").strip()
        experience = [dict(e) for e in (out.get("experience") or [])]
        target = _find_employer(experience, company)
        if target is None:
            # No matching employer: the achievement is kept rather than dropped,
            # in a place the user can see and fix. Silently discarding an
            # accepted change is worse than putting it slightly wrong.
            target = {"company": company or "Не указано", "role": "", "period": ""}
            experience.append(target)
        target["achievements"] = [*(target.get("achievements") or []), value]
        out["experience"] = experience
        provenance = dict(out.get("provenance") or {})
        entries = list(provenance.get("achievements") or [])
        entries.append(
            {"value": value, "company": company, "source": "user-stated", "at": stamp}
        )
        provenance["achievements"] = entries
        out["provenance"] = provenance
        return out

    if op == "set_field":
        field = str(arguments.get("field") or "").strip()
        # Only simple scalar fields: letting a chat replace `experience`
        # wholesale would make "additive only" meaningless.
        if field in {"target_role", "seniority", "field", "years_experience"}:
            out[field] = value
            provenance = dict(out.get("provenance") or {})
            fields = dict(provenance.get("fields") or {})
            fields[field] = {"source": "user-stated", "at": stamp}
            provenance["fields"] = fields
            out["provenance"] = provenance
        return out

    return out
