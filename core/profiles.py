"""Named run profiles: what the user picks instead of a pile of flags.

Borrowed from DeepSeek Harness, where a run is a named composition (`web`,
`headless`, `sdk`) rather than a command line assembled by hand. The same idea
fits here for a different reason: every step costs money, so the choice of
steps is a product decision the user makes, not a switch they tick.

Measured on 2026-09-12 — see docs/decisions.md: an evaluation takes ~13 s,
the full package ~54 s. The time is carried here so the UI can say how long a
click will take. The price is not: users see a share of their quota, never
money (2026-10-01).
"""

from __future__ import annotations

from dataclasses import dataclass

# The pipeline steps, in the order run_all executes them. "evaluate" is always
# first and always present: everything downstream consumes its report.
ALL_STEPS = ("evaluate", "tailor", "letter", "interview")


@dataclass(frozen=True)
class RunProfile:
    """One named way to run the pipeline."""

    name: str
    title: str
    steps: tuple[str, ...]
    seconds: int
    hint: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "steps": list(self.steps),
            "seconds": self.seconds,
            "hint": self.hint,
        }


PROFILES: dict[str, RunProfile] = {
    "evaluate": RunProfile(
        name="evaluate",
        title="Только оценка",
        steps=("evaluate",),
        seconds=13,
        hint="Балл, разбор требований, красные флаги. Это режим сканера.",
    ),
    "apply": RunProfile(
        name="apply",
        title="Готовлюсь откликнуться",
        steps=("evaluate", "tailor", "letter"),
        seconds=40,
        hint="Резюме под вакансию и сопроводительное письмо — поверх готовой оценки.",
    ),
    "all": RunProfile(
        name="all",
        title="Полный пакет",
        steps=ALL_STEPS,
        seconds=54,
        hint="То же плюс вопросы к интервью и STAR-истории.",
    ),
}

DEFAULT = "evaluate"

# Label and time per single step, because the screen buys them one at a time:
# each vacancy row carries its own buttons rather than a bundle selector.
STEP_INFO: dict[str, dict] = {
    "letter": {"title": "Сопровод", "seconds": 13,
               "hint": "Сопроводительное письмо под эту вакансию"},
    "tailor": {"title": "Резюме", "seconds": 14,
               "hint": "Резюме, переупакованное под эту вакансию"},
    "interview": {"title": "Интервью", "seconds": 14,
                  "hint": "Вопросы, которые зададут, и STAR-истории"},
}


def steps_as_list() -> list[dict]:
    """The buyable documents, for the row to render as buttons."""
    return [{"step": k, **v} for k, v in STEP_INFO.items()]


def get(name: str | None) -> RunProfile:
    """Resolve a profile by name, falling back to the cheapest one."""
    return PROFILES.get((name or DEFAULT).strip().lower(), PROFILES[DEFAULT])


def names() -> list[str]:
    return list(PROFILES)


def as_list() -> list[dict]:
    """All profiles, for the UI to render as a choice."""
    return [p.as_dict() for p in PROFILES.values()]
