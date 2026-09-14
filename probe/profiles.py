"""Named run profiles: what the user picks instead of a pile of flags.

Borrowed from DeepSeek Harness, where a run is a named composition (`web`,
`headless`, `sdk`) rather than a command line assembled by hand. The same idea
fits here for a different reason: every step costs money, so the choice of
steps is a product decision the user makes, not a switch they tick.

Measured on 2026-09-12 (off-peak, 95 RUB/USD) — see docs/decisions.md:
an evaluation is ~0.17 RUB / 13 s, the full package ~0.81 RUB / 54 s.
The numbers below are carried here so the UI can state the price before
the user pays it.
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
    rub: float
    seconds: int
    hint: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "steps": list(self.steps),
            "rub": self.rub,
            "seconds": self.seconds,
            "hint": self.hint,
        }


PROFILES: dict[str, RunProfile] = {
    "evaluate": RunProfile(
        name="evaluate",
        title="Только оценка",
        steps=("evaluate",),
        rub=0.17,
        seconds=13,
        hint="Балл, разбор требований, красные флаги. Это режим сканера.",
    ),
    "apply": RunProfile(
        name="apply",
        title="Готовлюсь откликнуться",
        steps=("evaluate", "tailor", "letter"),
        rub=0.64,
        seconds=40,
        hint="Оценка, резюме под вакансию и сопроводительное письмо. Без подготовки к интервью.",
    ),
    "all": RunProfile(
        name="all",
        title="Полный пакет",
        steps=ALL_STEPS,
        rub=0.81,
        seconds=54,
        hint="Всё сразу, включая вопросы к интервью и STAR-истории.",
    ),
}

DEFAULT = "evaluate"


def get(name: str | None) -> RunProfile:
    """Resolve a profile by name, falling back to the cheapest one."""
    return PROFILES.get((name or DEFAULT).strip().lower(), PROFILES[DEFAULT])


def names() -> list[str]:
    return list(PROFILES)


def as_list() -> list[dict]:
    """All profiles, for the UI to render as a choice."""
    return [p.as_dict() for p in PROFILES.values()]
