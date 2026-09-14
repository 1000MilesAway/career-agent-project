"""Free pre-screen filters. No LLM tokens spent here.

This mirrors how career-ops does it: the scanner collects postings over HTTP,
drops most of them with keyword and field checks, and only what survives is
worth paying a model to read. The order of checks follows scan.mjs.

Every rejection carries a reason, so a scan can report what it threw away and
the user can tell a working filter from an over-eager one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .sources.habr_api import QUAL_ORDER, Posting


@dataclass
class SearchProfile:
    """What the user is looking for. Generated from the CV, confirmed by the user."""

    queries: list[str] = field(default_factory=list)
    positive: list[str] = field(default_factory=list)
    negative: list[str] = field(default_factory=list)
    divisions_negative: list[str] = field(default_factory=list)
    qualifications: list[str] = field(default_factory=list)
    min_qualification: str = ""
    remote_only: bool = False
    salary_min: int | None = None
    max_age_days: int | None = None

    def as_dict(self) -> dict:
        return {
            "queries": self.queries,
            "positive": self.positive,
            "negative": self.negative,
            "divisions_negative": self.divisions_negative,
            "qualifications": self.qualifications,
            "min_qualification": self.min_qualification,
            "remote_only": self.remote_only,
            "salary_min": self.salary_min,
            "max_age_days": self.max_age_days,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SearchProfile:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


def _compile(keyword: str) -> re.Pattern:
    """Compile one keyword the way career-ops does.

    A `word:` prefix demands a whole word — needed because a short negative
    hides inside longer words ("QA" inside "Quality", "Go" inside "Google").
    Short keywords (2-3 chars) get word boundaries automatically for the same
    reason. Everything else matches as a substring.
    """
    raw = keyword.strip()
    whole = raw.startswith("word:")
    if whole:
        raw = raw[5:].strip()
    escaped = re.escape(raw)
    if whole or len(raw) <= 3:
        return re.compile(rf"(?<![\w-]){escaped}(?![\w-])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def _any_match(text: str, patterns: list[re.Pattern]) -> str | None:
    for p in patterns:
        if p.search(text):
            return p.pattern
    return None


@dataclass
class Decision:
    """Why a posting was kept or dropped."""

    keep: bool
    reason: str = ""


class PreScreen:
    """Runs a posting through the free checks, in career-ops order."""

    def __init__(self, profile: SearchProfile) -> None:
        self.profile = profile
        self._pos = [_compile(k) for k in profile.positive]
        self._neg = [_compile(k) for k in profile.negative]
        self._div_neg = [_compile(k) for k in profile.divisions_negative]

    def check(self, p: Posting) -> Decision:
        title = p.title or ""

        # 1. Negative title keywords — the user's own "not this" list.
        hit = _any_match(title, self._neg)
        if hit:
            return Decision(False, f"стоп-слово в заголовке: {hit}")

        # 2. Specialization. Habr states it per posting; a QA vacancy that
        #    merely mentions Python is caught here even when the title is clean.
        for d in p.divisions:
            hit = _any_match(d, self._div_neg)
            if hit:
                return Decision(False, f"не та специализация: {d}")

        # 3. Positive title keywords. Empty list means "no title requirement".
        if self._pos and not _any_match(title, self._pos):
            return Decision(False, "заголовок не совпал ни с одним ключевым словом")

        # 4. Qualification. Postings without a stated grade pass: on Habr a
        #    blank grade is common and usually means mid-level, so dropping
        #    them would hide real matches.
        if self.profile.min_qualification and p.qualification:
            want = self.profile.min_qualification.capitalize()
            if want in QUAL_ORDER and p.qualification in QUAL_ORDER:
                if QUAL_ORDER.index(p.qualification) < QUAL_ORDER.index(want):
                    return Decision(False, f"грейд ниже нужного: {p.qualification}")

        # 5. Remote.
        if self.profile.remote_only and not p.remote:
            return Decision(False, "не удалённая работа")

        # 6. Salary. Only judged when the employer stated a figure.
        if self.profile.salary_min:
            top = p.salary_to or p.salary_from
            if top and top < self.profile.salary_min:
                return Decision(False, f"зарплата ниже порога: {p.salary_text}")

        return Decision(True)

    def run(self, postings: list[Posting]) -> tuple[list[Posting], list[tuple[Posting, str]]]:
        """Split postings into survivors and (posting, reason) rejects."""
        kept: list[Posting] = []
        dropped: list[tuple[Posting, str]] = []
        for p in postings:
            d = self.check(p)
            if d.keep:
                kept.append(p)
            else:
                dropped.append((p, d.reason))
        return kept, dropped
