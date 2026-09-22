"""Salary parsing for sources that publish the band as PROSE, not as a field.

`PreScreen.salary_min` (core/filters.py) reads `Posting.salary_from/salary_to`.
Habr's JSON API hands those over as numbers. Telegram does not: a post writes
its band however the author typed it, so the salary check was inert on the one
source where the band is stated most informally — every post passed as "no
salary data". This module is the parser that gives that check something to read.

Ported from career-ops `providers/_salary-text.mjs`, including its two guard
rails, both of which exist because of posts observed live on configured
channels (see docs/decisions.md).

WHAT IT DOES NOT DO: it never guesses. A line it cannot read confidently
returns None, which the filter treats as "no salary stated" and passes — the
same conservative default as a source that exposes nothing. Under-filtering
shows one extra posting; over-filtering hides a real job, and a figure invented
from a misparse would travel on into evaluation and the report as published
fact.

Units: the Russian market quotes MONTHLY. Everything here is returned in the
unit the posting stated, monthly-normalized, because that is what a user types
into `salary_min`. (career-ops annualizes instead, because its filter is annual
— the one deliberate divergence in this port.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Currency detection. Each token is distinct, so order only affects display.
CURRENCY_TOKENS = [
    (re.compile(r"₽|руб|rub|р\.", re.IGNORECASE), "RUB"),
    (re.compile(r"\$|usd|долл", re.IGNORECASE), "USD"),
    (re.compile(r"€|eur|евро", re.IGNORECASE), "EUR"),
]

# "Зарплата: не указана" is a POSITIVE statement that there is no band — common
# on channels republishing hh.ru. Without this, the digits left on the line
# ("Требуемый опыт: 3–6 лет", "Дата публикации: 13.08.2026") are all a number
# parser sees.
NOT_STATED_RE = re.compile(
    r"(?:зарплат\w*|зп|з/п|вилк\w*|salary|оклад)\s*[:\-–—]?\s*"
    r"(?:не\s*указан\w*|по\s*договор[её]нности|по\s*итогам\s*собеседовани\w*"
    r"|обсуждается|negotiable|not\s*specified|competitive)",
    re.IGNORECASE,
)

# Lines carrying a number that is NOT this posting's band:
#   - somebody else's pay (the mentors on an internship, "у коллег"), or a band
#     that only applies after probation — both overstate the offer;
#   - a PRICE, not a wage. A course ad that slipped through reads "1200 € или
#     120 000 ₽, возможна оплата частями", which parses into a plausible band.
#     Money the candidate PAYS must never be read as money they earn.
NOT_THIS_ROLE_RE = re.compile(
    r"руководител\w*\s+стажировк|наставник\w*|ментор\w*|у\s+коллег"
    r"|после\s+стажировк|после\s+испытательн\w*|после\s+прохождени\w*"
    r"|средн\w*\s+(?:зарплат\w*|по\s+рынку)|стоимост\w*|цена|оплата\s+частями"
    r"|рассрочк\w*|скидк\w*|тариф\w*|за\s+курс|стоит\s+\d|обучени\w*\s+сто"
    r"|price|tuition|instal?lments?",
    re.IGNORECASE,
)

# A label introducing a band. Required when the line carries no currency symbol,
# so a bare "3–6 лет" or "от 3 лет опыта" is not read as money.
#
# The separator after the label is OPTIONAL, unlike in career-ops, which
# requires a colon or dash. Their input is a card's salary cell, where the label
# is always formatted; ours is a post body, where "Оклад 150к" and "зарплата
# от 200 000" are written plainly and would otherwise never be read.
SALARY_LABEL_RE = re.compile(
    r"(?:зарплат\w*|зп|з/п|вилк\w*|оклад|доход|компенсаци\w*|ставк\w*"
    r"|уровень\s+зарплат\w*|salary|compensation|pay)\s*[:\-–—]?\s*(?:от|до|\d)",
    re.IGNORECASE,
)

# A thousands suffix is itself enough to mark a line as money: "350–380 к" and
# "от 180 тыс" carry neither a currency sign nor a label, and are unambiguous —
# no experience span, date or headcount is ever written with one.
THOUSANDS_LINE_RE = re.compile(r"\d\s*(?:к|k|тыс\.?|тысяч\w*|тр)(?![^\W\d_]|\d)", re.IGNORECASE)

# "к"/"k"/"тыс" scale a number by a thousand. Posts mix all three, sometimes in
# one line. Anchored at the start of the text FOLLOWING the digits.
THOUSAND_SUFFIX_RE = re.compile(r"^\s*(?:к|k|тыс\.?|тысяч\w*|тр)(?![^\W\d_]|\d)", re.IGNORECASE)

# Period markers. An explicit one always beats the monthly default.
PER_YEAR_RE = re.compile(
    r"в\s*год|/\s*год|годов\w*|per\s*year|annual|p\.?a\.?|/\s*year|yearly", re.IGNORECASE
)
PER_MONTH_RE = re.compile(
    r"в\s*месяц|/\s*мес|ежемесячн\w*|per\s*month|monthly|/\s*month|мес\.", re.IGNORECASE
)
# Per hour or per day needs an hours-per-week assumption to normalize. Refuse.
PER_HOUR_RE = re.compile(
    r"в\s*час|/\s*час|per\s*hour|hourly|/\s*hour|в\s*день|per\s*day", re.IGNORECASE
)

MONTHS_PER_YEAR = 12

# Plausibility window, per currency, applied to the MONTHLY figure. Its job is
# to catch a misparse, not to judge an offer: a number outside these bounds is
# far likelier a phone fragment, a headcount, a year or a bad unit guess than a
# real salary. Out of range → None → "no salary data" → the posting passes.
SANITY_BOUNDS = {
    "RUB": (10_000, 5_000_000),
    "USD": (250, 170_000),
    "EUR": (250, 170_000),
}

# A number immediately followed by one of these is an experience span or an age,
# not money.
NOT_MONEY_TAIL_RE = re.compile(r"^\s*(?:лет|год|года|years?|мес\w*\s+опыт)", re.IGNORECASE)

DATE_RE = re.compile(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}")
NUMBER_RE = re.compile(r"\d[\d\s  '.,]*\d|\d")
FROM_RE = re.compile(r"(?:^|[\s(])(?:от|from)\s*$", re.IGNORECASE)
TO_RE = re.compile(r"(?:^|[\s(])(?:до|to|up\s+to)\s*$", re.IGNORECASE)
DASH_RE = re.compile(r"[-–—]")


@dataclass(frozen=True)
class Band:
    """A salary band, monthly, in the currency the posting stated."""

    min: int
    max: int
    currency: str


@dataclass(frozen=True)
class _Amount:
    value: float
    start: int
    end: int
    scaled: bool


def detect_currency(text: str) -> str:
    """The currency named in the text, or '' when nothing names one."""
    for pattern, code in CURRENCY_TOKENS:
        if pattern.search(text):
            return code
    return ""


def _read_amount(digits: str, tail: str) -> float | None:
    """A number that may carry separators and a thousands suffix.

    "700 000", "180 тыс" and "350к" are all the same magnitude. A comma or dot
    BETWEEN digit groups is a separator too ("370.000" is 370 000, not 370) —
    but a decimal comma with one or two trailing digits ("2,5к") is a fraction.
    """
    normalized = re.sub(r"[\s  ']", "", digits)
    decimal = re.fullmatch(r"(\d+)[.,](\d{1,2})", normalized)
    if decimal:
        value = float(f"{decimal.group(1)}.{decimal.group(2)}")
    else:
        value = float(re.sub(r"[.,]", "", normalized) or 0)
    if value <= 0:
        return None
    if THOUSAND_SUFFIX_RE.search(tail):
        value *= 1000
    return value


def monthly_divisor(text: str, currency: str, amount: float) -> float | None:
    """How much to divide by to get a MONTHLY figure. None = refuse to normalize.

    Monthly is the Russian-market default. An explicit period marker wins over
    it. USD/EUR without one is ambiguous — "5000-7500 USD" is monthly on a
    Russian-market channel, "$180,000" is annual on a Western posting — so it
    splits on magnitude, the same threshold career-ops uses.
    """
    if PER_HOUR_RE.search(text):
        return None
    if PER_YEAR_RE.search(text):
        return MONTHS_PER_YEAR
    if PER_MONTH_RE.search(text):
        return 1
    if currency == "RUB":
        return 1
    return MONTHS_PER_YEAR if amount >= 30_000 else 1


def _scale_range_units(amounts: list[_Amount]) -> list[float]:
    """Apply a range's unit to every figure in it.

    A Russian post writes the unit ONCE, after the last figure: "от 185 до 215
    тыс руб", "350–380 к". Scaling only the figure the suffix touches yields a
    band three orders of magnitude wide (185 ₽ to 215 000 ₽) — and the bogus low
    end is exactly what a min-salary filter compares against.
    """
    values = [a.value for a in amounts]
    largest = max(values)
    out = []
    for a in amounts:
        if a.value == largest:
            out.append(a.value)
            continue
        # Two orders of magnitude apart inside one band is never how a real one
        # is written — it is the missing unit. One order (150 000–900 000) is.
        if a.value > 0 and largest / a.value >= 100:
            scaled = a.value * 1000
            # Only accept the rescue if it lands beside the other end.
            if scaled <= largest:
                out.append(scaled)
                continue
        out.append(a.value)
    return out


def _parse_band_from_line(line: str, detected_currency: str) -> Band | None:
    # Drop dates, so "Дата публикации: 13.08.2026" cannot contribute digits to a
    # line that also carries a real band.
    cleaned = DATE_RE.sub(" ", line)

    amounts: list[_Amount] = []
    for m in NUMBER_RE.finditer(cleaned):
        tail = cleaned[m.end() : m.end() + 12]
        if NOT_MONEY_TAIL_RE.search(tail):
            continue
        value = _read_amount(m.group(0), tail)
        if value is None:
            continue
        amounts.append(
            _Amount(value, m.start(), m.end(), bool(THOUSAND_SUFFIX_RE.search(tail)))
        )
    if not amounts:
        return None

    currency = detected_currency or "RUB"
    divisor = monthly_divisor(cleaned, currency, max(a.value for a in amounts))
    if divisor is None:
        return None

    # Each keyword counts only where it ACTUALLY QUALIFIES A NUMBER, not
    # anywhere on the line: "до 370.000 (в зависимости от уровня кандидата)"
    # carries a prose "от" that would otherwise turn a ceiling into a floor.
    def before(a: _Amount) -> str:
        return cleaned[max(0, a.start - 10) : a.start]

    has_from = any(FROM_RE.search(before(a)) for a in amounts)
    has_to = any(TO_RE.search(before(a)) for a in amounts)

    # A dash range is detected on the TEXT BETWEEN two parsed numbers, so a
    # currency sign in that gap ("2000$-4500$") cannot hide it.
    dash_between = any(
        len(gap) <= 12 and DASH_RE.search(gap)
        for gap in (cleaned[amounts[i - 1].end : amounts[i].start] for i in range(1, len(amounts)))
    )
    is_range = len(amounts) >= 2 and (dash_between or (has_from and has_to))

    if is_range:
        values = _scale_range_units(amounts)
        low, high = min(values), max(values)
    elif has_to and not has_from:
        low, high = 0.0, max(a.value for a in amounts)
    else:
        # "от X" or a bare figure: a floor with no stated ceiling.
        low = high = max(a.value for a in amounts)

    monthly_min = low / divisor
    monthly_max = high / divisor

    bounds = SANITY_BOUNDS.get(currency)
    if bounds:
        # A "до X" band has min 0 by construction, so judge it on the ceiling.
        checked = monthly_max if monthly_max > 0 else monthly_min
        if checked < bounds[0] or checked > bounds[1]:
            return None

    return Band(min=round(monthly_min), max=round(monthly_max), currency=currency)


def parse_salary(text: str) -> Band | None:
    """A salary band out of free text, or None when nothing is confidently read.

    None is the safe answer — the filter passes a posting with no salary data.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    # Line by line: a post body mixes the band with experience ranges, dates and
    # headcounts, and only the line carrying the band is worth reading.
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or len(line) > 300:
            continue
        if NOT_STATED_RE.search(line):
            continue
        if NOT_THIS_ROLE_RE.search(line):
            continue

        currency = detect_currency(line)
        # No currency symbol, no salary label and no thousands unit: an
        # experience range, a date or a count, not a band.
        if (
            not currency
            and not SALARY_LABEL_RE.search(line)
            and not THOUSANDS_LINE_RE.search(line)
        ):
            continue

        band = _parse_band_from_line(line, currency)
        if band:
            return band

    return None
