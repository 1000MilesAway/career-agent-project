"""Repair state that was written before the filters were fixed.

Two separate problems, both from 2026-09-14, both fixable without the model:

1. The accepted search profile in `out/resume-state.json` was merged with the
   old `accept()`, which REPLACED the guard lists instead of unioning them. The
   template's roots ("Фронтенд", "Мобильн") were lost, so every later scan let
   frontend, mobile and DevOps postings straight through.

2. `out/found.json` therefore accumulated postings the free filters should have
   dropped. Re-running the fixed filter over the stored rows costs nothing.

Nothing here calls the LLM, and nothing throws away an evaluation the user has
already paid for — a dropped row keeps its place if it carries a report.
"""

from __future__ import annotations

from dataclasses import dataclass

from .defaults import starter_profile
from .filters import PreScreen, SearchProfile
from .proposals import merge_profile
from .sources.habr_api import Posting


def repair_profile(stored: dict | None) -> dict:
    """Union the shipped template back into a stored search profile.

    The template goes first (it owns the classic traps), the stored profile is
    laid over it, so `queries` — where the model read the CV — still wins.

    `positive` is unioned here even though merge_profile replaces it. The two
    situations differ: accepting a fresh proposal is the model's considered
    answer about this CV, but a REPAIR is undoing a loss, and the template's
    keywords are what went missing. Keeping only the stored list would leave
    "MLOps-инженер" and "ML-инженер" unmatched, which is how two live ML
    vacancies were dropped on 2026-09-14.
    """
    template = starter_profile().as_dict()
    if not stored:
        return template

    fixed = merge_profile(template, dict(stored))
    fixed["positive"] = _union(template.get("positive"), stored.get("positive"))
    return fixed


def _union(first: list | None, second: list | None) -> list:
    """Both lists, template order first, duplicates dropped case-insensitively."""
    out = list(first or [])
    seen = {str(v).casefold() for v in out}
    for value in second or []:
        if str(value).casefold() not in seen:
            seen.add(str(value).casefold())
            out.append(value)
    return out


def row_to_posting(row: dict) -> Posting:
    """A stored found.json row, back into the shape the filters expect."""
    return Posting(
        id=str(row.get("id") or ""),
        url=row.get("url") or "",
        title=row.get("title") or "",
        company=row.get("company") or "",
        qualification=row.get("qualification") or "",
        remote=bool(row.get("remote")),
        location=row.get("location") or "",
        salary_from=row.get("salary_from"),
        salary_to=row.get("salary_to"),
        salary_text=row.get("salary_text") or "",
        divisions=row.get("divisions") or [],
        skills=row.get("skills") or [],
        published=row.get("published") or "",
    )


@dataclass
class Cleanup:
    """What re-filtering the found list would do."""

    kept: list[dict]
    removed: list[tuple[dict, str]]
    protected: list[dict]

    @property
    def summary(self) -> str:
        line = f"оставляем {len(self.kept)}, убираем {len(self.removed)}"
        if self.protected:
            line += f", сохранено оплаченных оценок: {len(self.protected)}"
        return line


def clean_found(rows: list[dict], profile: SearchProfile) -> Cleanup:
    """Re-run the free filters over stored rows.

    A row that carries a `report` was paid for and is never removed, however
    badly it matches now: deleting it would mean charging the user twice to see
    the same verdict. It is reported in `protected` so the caller can say so.
    """
    screen = PreScreen(profile)
    kept: list[dict] = []
    removed: list[tuple[dict, str]] = []
    protected: list[dict] = []

    for row in rows:
        decision = screen.check(row_to_posting(row))
        if decision.keep:
            kept.append(row)
        elif row.get("report"):
            protected.append(row)
            kept.append(row)
        else:
            removed.append((row, decision.reason))

    return Cleanup(kept=kept, removed=removed, protected=protected)
