"""schema.org JobPosting out of a vacancy page's JSON-LD.

Why this format and not per-site markup: Google for Jobs indexes only postings
that carry it, so job boards and career sites keep it correct long after their
visible markup has been redesigned twice. geekjob.ru, most ATS-hosted career
pages and many employer sites serve it. One reader covers all of them, and a
redesign of a site's CSS does not touch it.

Used twice:
  - to read a vacancy's full text for evaluation (`as_text`);
  - to tell whether a vacancy is still open (`validThrough`, and whether the
    page carries a posting at all) — see core/liveness.py.

Nothing here does network. Callers fetch; this module only parses, so it is
testable on saved pages.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from selectolax.parser import HTMLParser

LD_SELECTOR = 'script[type="application/ld+json"]'

# The model reads prose, and a description a page ships as 40 KB of HTML list
# markup would crowd the rest of a triage batch out. Same ceiling as a
# Telegram description page.
MAX_DESCRIPTION_CHARS = 8000


@dataclass
class JobPosting:
    title: str
    company: str = ""
    description: str = ""
    date_posted: str = ""
    valid_through: str = ""
    location: str = ""
    remote: bool = False
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str = ""
    employment_type: str = ""

    def expired(self, today: date | None = None) -> bool:
        """True only when the page itself says the posting has run out.

        No date, or a date we cannot read, is NOT expired: a missing field is
        an absence of evidence, and calling a live vacancy closed hides it.
        """
        if not self.valid_through:
            return False
        try:
            end = datetime.fromisoformat(self.valid_through.replace("Z", "+00:00"))
        except ValueError:
            return False
        today = today or datetime.now(UTC).date()
        return end.date() < today

    def as_text(self) -> str:
        """What the model reads: the fields first, then the description."""
        lines = [self.title]
        if self.company:
            lines.append(f"Компания: {self.company}")
        if self.location:
            lines.append(f"Локация: {self.location}")
        if self.remote:
            lines.append("Формат: удалённо")
        if self.salary_from or self.salary_to:
            ends = [x for x in (self.salary_from, self.salary_to) if x]
            band = "–".join(str(x) for x in dict.fromkeys(ends))
            lines.append(f"Зарплата: {band} {self.currency}".rstrip())
        if self.employment_type:
            lines.append(f"Занятость: {self.employment_type}")
        if self.description:
            lines += ["", self.description]
        return "\n".join(lines)


def _html_to_text(fragment: str) -> str:
    if not fragment:
        return ""
    # Descriptions arrive as HTML inside a JSON string. <br> and block ends
    # must become newlines before the tags go, or list items glue together.
    fragment = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|div|ul|ol)>", "\n", fragment)
    text = HTMLParser(f"<div>{fragment}</div>").text(separator="")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()[:MAX_DESCRIPTION_CHARS]


def _walk(node: Any):
    """Every dict in a JSON-LD blob. Sites nest postings in @graph or lists."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_posting(node: dict) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return "JobPosting" in kinds


def _name(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("name") or "").strip()
    if isinstance(node, str):
        return node.strip()
    return ""


def _location(node: Any) -> str:
    places = node if isinstance(node, list) else [node]
    out: list[str] = []
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get("address")
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                _name(address.get("addressCountry")) or address.get("addressCountry"),
            ]
            text = ", ".join(str(p) for p in parts if p and isinstance(p, str))
        else:
            text = _name(address) or _name(place)
        if text and text not in out:
            out.append(text)
    return "; ".join(out)


def _number(value: Any) -> int | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return int(n) if n > 0 else None


def _salary(node: Any) -> tuple[int | None, int | None, str]:
    """Monthly band from baseSalary, or Nones when the unit is not a month.

    An hourly or yearly figure passed on as-is would read as a monthly salary
    to PreScreen, so anything not stated per month is dropped rather than
    guessed at — the salary filter then simply does not fire.
    """
    if not isinstance(node, dict):
        return None, None, ""
    currency = str(node.get("currency") or "")
    value = node.get("value")
    unit = ""
    if isinstance(value, dict):
        unit = str(value.get("unitText") or "").upper()
        low = _number(value.get("minValue") or value.get("value"))
        high = _number(value.get("maxValue") or value.get("value"))
    else:
        low = high = _number(value)
    if unit == "YEAR":
        low = low // 12 if low else None
        high = high // 12 if high else None
    elif unit not in ("", "MONTH"):
        return None, None, currency
    return low, high, currency


def parse(html: str) -> JobPosting | None:
    """The first JobPosting on the page, or None when there is none.

    A broken JSON-LD block is skipped, not fatal: sites ship several blocks
    (breadcrumbs, organization) and one hand-written bad one must not hide the
    posting next to it.
    """
    tree = HTMLParser(html)
    for script in tree.css(LD_SELECTOR):
        raw = script.text(strip=True)
        if not raw:
            continue
        try:
            # strict=False: raw newlines inside strings are common in the wild.
            data = json.loads(raw, strict=False)
        except ValueError:
            continue
        for node in _walk(data):
            if _is_posting(node):
                return _from_node(node)
    return None


def _from_node(node: dict) -> JobPosting:
    low, high, currency = _salary(node.get("baseSalary"))
    location_type = str(node.get("jobLocationType") or "").upper()
    employment = node.get("employmentType") or ""
    if isinstance(employment, list):
        employment = ", ".join(str(e) for e in employment)
    return JobPosting(
        title=str(node.get("title") or "").strip(),
        company=_name(node.get("hiringOrganization")),
        description=_html_to_text(str(node.get("description") or "")),
        date_posted=str(node.get("datePosted") or ""),
        valid_through=str(node.get("validThrough") or ""),
        location=_location(node.get("jobLocation")),
        remote=location_type == "TELECOMMUTE",
        salary_from=low,
        salary_to=high,
        currency=currency,
        employment_type=str(employment),
    )
