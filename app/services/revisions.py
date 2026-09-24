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


def changes(resume: Resume, parent: Resume | None) -> list[str]:
    """What this revision changed, in words, for the history list.

    Skills and achievements only — the two things a chat edit adds. A field a
    rollback swapped wholesale shows up the same way, as removals and
    additions, which is what the user needs to recognise the version.
    """
    if parent is None:
        return [f"Загружен файл «{resume.filename}»"]

    new, old = resume.profile or {}, parent.profile or {}
    out: list[str] = []
    new_skills = [str(s) for s in new.get("skills") or []]
    old_skills = [str(s) for s in old.get("skills") or []]
    out += [f"+ навык: {s}" for s in _added(new_skills, old_skills)]
    out += [f"− навык: {s}" for s in _added(old_skills, new_skills)]
    new_ach, old_ach = _achievements(new), _achievements(old)
    out += [f"+ {s}" for s in _added(new_ach, old_ach)]
    out += [f"− {s}" for s in _added(old_ach, new_ach)]

    for field in ("target_role", "seniority", "field", "years_experience"):
        if new.get(field) != old.get(field):
            out.append(f"{field}: {old.get(field) or '—'} → {new.get(field) or '—'}")

    if resume.filename != parent.filename:
        out.insert(0, f"Файл «{resume.filename}»")
    return out or ["Без изменений в навыках и опыте"]
