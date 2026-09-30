"""Resume revisions: a new row per change, never an edit in place.

Why rows and not edits: a report bought against a profile must keep matching
the profile it read, and a new resume_id is what lets the user buy a fresh
evaluation of the same vacancy. See docs/decisions.md, 2026-09-22 (variant B).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Resume


async def new_revision(
    session: AsyncSession,
    current: Resume,
    *,
    profile: dict[str, Any],
    filename: str | None = None,
    raw_text: str | None = None,
) -> Resume:
    """Make `profile` the active CV, as the next revision after `current`.

    `search_profile` carries over: the user confirmed it, and nothing here is
    a reason to lose that. `archetypes` does not: the portrait triage screens
    against was derived from the old profile.
    """
    current.is_active = False
    # Flushed before the insert: one active CV per user is a unique index,
    # and two active rows for a moment is enough to trip it.
    await session.flush()
    revision = Resume(
        user_id=current.user_id,
        filename=filename or current.filename,
        raw_text=raw_text if raw_text is not None else current.raw_text,
        profile=profile,
        search_profile=current.search_profile,
        archetypes=None,
        is_active=True,
        parent_id=current.id,
        revision=current.revision + 1,
    )
    session.add(revision)
    return revision


async def restore(session: AsyncSession, current: Resume, target: Resume) -> Resume:
    """Roll back to `target` by copying it forward, not by reactivating it.

    A copy keeps history linear and makes the rollback itself undoable — the
    two conditions the user set for B. Reactivating the old row would have
    brought its evaluations back, but also a history where "current" jumps
    backwards and the version being left is lost from the chain.
    """
    return await new_revision(
        session,
        current,
        profile=dict(target.profile or {}),
        filename=target.filename,
        raw_text=target.raw_text,
    )


def _achievements(profile: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for job in profile.get("experience") or []:
        company = str(job.get("company") or "").strip()
        for item in job.get("achievements") or []:
            out.append(f"{company}: {item}" if company else str(item))
    return out


def _added(new: list[str], old: list[str]) -> list[str]:
    seen = {s.casefold() for s in old}
    return [s for s in new if s.casefold() not in seen]


# Russian labels for the history list; a raw key like "target_role" means
# nothing to the user reading it.
_FIELDS = {
    "name": "имя",
    "target_role": "должность",
    "seniority": "уровень",
    "field": "сфера",
    "years_experience": "лет опыта",
}
_LISTS = {
    "directions": "направление",
    "education": "образование",
    "languages": "язык",
}


def _job_key(job: dict[str, Any]) -> str:
    return str(job.get("company") or job.get("role") or "").strip()


def _strings(profile: dict[str, Any], key: str) -> list[str]:
    return [str(s) for s in profile.get(key) or []]


def changes(resume: Resume, parent: Resume | None) -> list[str]:
    """What this revision changed, in words, for the history list.

    Additions and removals are named item by item — skills and achievements
    are what a chat edit adds, and a rollback shows up the same way, which is
    what the user needs to recognise the version. A hand edit can touch any
    field, so the rest is named by the field it changed.
    """
    if parent is None:
        return [f"Загружен файл «{resume.filename}»"]

    new, old = resume.profile or {}, parent.profile or {}
    out: list[str] = []
    new_skills, old_skills = _strings(new, "skills"), _strings(old, "skills")
    out += [f"+ навык: {s}" for s in _added(new_skills, old_skills)]
    out += [f"− навык: {s}" for s in _added(old_skills, new_skills)]
    new_ach, old_ach = _achievements(new), _achievements(old)
    out += [f"+ {s}" for s in _added(new_ach, old_ach)]
    out += [f"− {s}" for s in _added(old_ach, new_ach)]

    for key, label in _LISTS.items():
        new_items, old_items = _strings(new, key), _strings(old, key)
        out += [f"+ {label}: {s}" for s in _added(new_items, old_items)]
        out += [f"− {label}: {s}" for s in _added(old_items, new_items)]

    new_jobs = {_job_key(j): j for j in new.get("experience") or []}
    old_jobs = {_job_key(j): j for j in old.get("experience") or []}
    out += [f"+ место работы: {k or '—'}" for k in new_jobs if k not in old_jobs]
    out += [f"− место работы: {k or '—'}" for k in old_jobs if k not in new_jobs]
    for key, job in new_jobs.items():
        before = old_jobs.get(key)
        if before is None:
            continue
        # Achievements are already listed one by one above.
        fields = ("role", "period", "location", "description")
        if any(job.get(f) != before.get(f) for f in fields):
            out.append(f"изменено место работы: {key or '—'}")

    for key, label in _FIELDS.items():
        if new.get(key) != old.get(key):
            out.append(f"{label}: {old.get(key) or '—'} → {new.get(key) or '—'}")
    if new.get("summary") != old.get("summary"):
        out.append("изменён абзац «О себе»")

    if resume.filename != parent.filename:
        out.insert(0, f"Файл «{resume.filename}»")
    return out or ["Без изменений в навыках и опыте"]
