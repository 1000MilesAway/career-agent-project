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

# Positive keywords that name no technology and no specific role. They are
# useful for "is this a dev job at all", but they say nothing about WHICH kind,
# so they must not be allowed to overrule a stated specialization: otherwise
# "Фронтенд разработчик" protects itself with the word "разработчик".
GENERIC_POSITIVE = {
    "разработчик",
    "программист",
    "инженер",
    "developer",
    "engineer",
    "software engineer",
    "инженер-программист",
}


@dataclass
class SearchProfile:
    """What the user is looking for. Generated from the CV, confirmed by the user."""

    queries: list[str] = field(default_factory=list)
    positive: list[str] = field(default_factory=list)
    negative: list[str] = field(default_factory=list)
    divisions_negative: list[str] = field(default_factory=list)
    # Divisions that name a NEIGHBOURING STACK rather than another profession.
    # These lose to a specific positive keyword in the title; see PreScreen.
    divisions_soft: list[str] = field(default_factory=list)
    # Divisions the candidate targets. A generic title ("Разработчик") passes
    # only inside one of these; see PreScreen.check step 3.
    divisions_positive: list[str] = field(default_factory=list)
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
            "divisions_soft": self.divisions_soft,
            "divisions_positive": self.divisions_positive,
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


# Latin letters that look identical to Cyrillic ones. Employers type them by
# accident (or to dodge filters): "Rust paзработчик" on 2026-09-15 had a Latin
# "a" inside an otherwise Cyrillic word, and every keyword silently missed it.
# Folding the Latin twin onto its Cyrillic look-alike makes both spellings match.
#
# Folding happens on the lower-cased string only. Mapping both cases separately
# would be a trap: "P"->"Р" and "p"->"р" are a proper case pair, but a half-filled
# table (say "y" without "Y") leaves the two cases folding to different letters,
# and re.IGNORECASE cannot bring them back together — "python" then stops
# matching "PYTHON".
# Only pairs that are twins in LOWER case belong here. "B"/"В" and "H"/"Н" look
# identical capitalised but not as "b"/"ь", "h"/"н" — folding those mangles
# ordinary Latin words ("Backend" -> "ьаскеnd") and eats real keywords: it cost
# the stop-word "yii" its match against "php (yii2)".
LOOKALIKES = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
})


def _fold(text: str) -> str:
    """Normalise Latin/Cyrillic look-alikes so one spelling matches both.

    Case is dropped here, so every pattern is compiled with re.IGNORECASE and
    both sides arrive lower-cased.
    """
    return text.casefold().translate(LOOKALIKES)


class _Folded:
    """A compiled keyword that folds look-alikes on both sides before matching.

    Wrapping re.Pattern rather than folding at the call site keeps every user of
    `_compile` honest: a direct `.search(title)` cannot forget to normalise and
    silently miss "Rust paзработчик".
    """

    __slots__ = ("_re", "pattern")

    def __init__(self, compiled: re.Pattern) -> None:
        self._re = compiled
        self.pattern = compiled.pattern

    def search(self, text: str):
        return self._re.search(_fold(text))


class _AndGroup:
    """Several terms that must ALL appear, in any order.

    Same surface as _Folded (`.search`, `.pattern`) so callers stay unchanged.
    """

    __slots__ = ("_parts", "pattern")

    def __init__(self, parts: list[_Folded], pattern: str) -> None:
        self._parts = parts
        self.pattern = pattern

    def search(self, text: str):
        return all(p.search(text) for p in self._parts) or None


# " + " with surrounding whitespace, never a bare "+": splitting on the bare
# sign would turn the ordinary keyword "C++" into "c", which matches nearly
# every title — a silent flood in place of a silent miss.
AND_SEPARATOR = re.compile(r"\s+\+\s+")


def _compile(keyword: str) -> _Folded | _AndGroup:
    """Compile one keyword the way career-ops does.

    Three prefixes, in rising strictness — the middle one exists because a plain
    keyword is loose on BOTH sides and usually only one of them was meant:

        Rust        "Rust Engineer" ✓  "Zero Trust Engineer" ✓  <- default
        stem:Rust   "Rust Engineer" ✓  "Zero Trust Engineer" ✗  <- must START a word
        word:Rust   "Rust Engineer" ✓  "Rust Engineering"    ✗  <- the whole word

    So `stem:` is for a keyword that begins a longer word you also want
    ("Крипто" reaching "Криптовалюта") without landing mid-word in an unrelated
    one. `word:` also forbids the suffix, which costs plural and compound forms.
    Short keywords (2-3 chars) are anchored automatically: "QA" must not fire
    inside "Quality", nor "Go" inside "Google".

    A " + " between terms means ALL of them must appear, in any order. One entry
    replaces a whole matrix of spellings: "разработчик + java" covers "Java
    Developer", "Java-разработчик" and "Ведущий разработчик Java" alike, where a
    list of literal forms silently misses whichever variant it does not contain.
    """
    raw = keyword.strip()
    if AND_SEPARATOR.search(raw):
        terms = [t for t in AND_SEPARATOR.split(raw) if t.strip()]
        if len(terms) > 1:
            return _AndGroup([_compile(t) for t in terms], raw)

    whole = raw.startswith("word:")
    stem = raw.startswith("stem:")
    if whole or stem:
        raw = raw[5:].strip()
    escaped = re.escape(_fold(raw))
    if stem:
        return _Folded(re.compile(rf"(?<![\w-]){escaped}", re.IGNORECASE))
    if whole or len(raw) <= 3:
        return _Folded(re.compile(rf"(?<![\w-]){escaped}(?![\w-])", re.IGNORECASE))
    return _Folded(re.compile(escaped, re.IGNORECASE))


def _any_match(text: str, patterns: list[_Folded]) -> str | None:
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
        self._div_soft = [_compile(k) for k in profile.divisions_soft]
        self._div_pos = [_compile(k) for k in profile.divisions_positive]
        # Positive keywords strong enough to overrule a neighbouring-stack division.
        self._specific_pos = [
            _compile(k) for k in profile.positive if k.casefold() not in GENERIC_POSITIVE
        ]

    def check(self, p: Posting) -> Decision:
        title = p.title or ""

        # 1. Negative title keywords — the user's own "not this" list.
        hit = _any_match(title, self._neg)
        if hit:
            return Decision(False, f"стоп-слово в заголовке: {hit}")

        # 2. Specialization, in two strengths. Habr states it per posting.
        #
        #    ANOTHER PROFESSION (QA, analytics, sales) always wins: this is the
        #    case the filter was written for — a QA vacancy whose title merely
        #    mentions Python. No keyword in the title can vouch for it, because
        #    the title of a QA job legitimately says "Python".
        for d in p.divisions:
            hit = _any_match(d, self._div_neg)
            if hit:
                return Decision(False, f"не та специализация: {d}")

        #    A NEIGHBOURING STACK (mobile, desktop, embedded, DB) is weaker.
        #    Employers misfile these constantly: on 2026-09-14 "Python
        #    разработчик" sat under "Разработчик мобильных приложений" and
        #    "Software engineer (Go\\Python)" under "Инженер встраиваемых
        #    систем". Both are our candidate's work, wrongly labelled — so a
        #    specific technology in the title overrules the label.
        #
        #    Generic words cannot: "Разработчик" matches "Фронтенд
        #    разработчик" too, which would void the check for nearly everything.
        if not _any_match(title, self._specific_pos):
            for d in p.divisions:
                hit = _any_match(d, self._div_soft)
                if hit:
                    return Decision(False, f"не та специализация: {d}")

        # 3. Positive title keywords. Empty list means "no title requirement".
        #
        #    A GENERIC word is not enough on its own. "Разработчик", "Developer"
        #    and "Engineer" appear in every stack there is, so on 2026-09-15 they
        #    waved through Hadoop, BI, BPMN, C++ and Rust vacancies — none of
        #    them this candidate's work. A generic title passes only when the
        #    stated division is one we target; a specific keyword always passes.
        if self._pos and not _any_match(title, self._pos):
            return Decision(False, "заголовок не совпал ни с одним ключевым словом")

        #    A posting with no division stated is exempt: the check would then
        #    be judging an absence, and Habr postings do come without one.
        if self._pos and p.divisions and not _any_match(title, self._specific_pos):
            if self._div_pos and not any(_any_match(d, self._div_pos) for d in p.divisions):
                return Decision(
                    False,
                    f"общий заголовок вне целевой специализации: {', '.join(p.divisions)}",
                )

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
