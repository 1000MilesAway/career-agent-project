"""Habr Career via its public JSON API.

Why JSON and not the HTML we parsed first: the API hands back qualification,
remote flag, salary as numbers, skills and specialization as fields, so the
free filters below can work on real data instead of guessed-at markup.

Verified 2026-09-12 against the live API:
  - `qid[]` (3=Junior, 4=Middle, 5=Senior) and `remote=true` really filter.
  - `specializations[]` / `spec` are IGNORED — they silently return the whole
    catalogue. Never trust them; specialization is filtered locally from the
    `divisions` field instead.
  - `meta.totalResults` / `meta.totalPages` drive paging.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

BASE = "https://career.habr.com"
API = f"{BASE}/api/frontend/vacancies"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DELAY_SECONDS = 1.0
PER_PAGE = 25

# Habr's qualification ids.
QUALIFICATION = {"intern": 1, "junior": 3, "middle": 4, "senior": 5}
QUAL_ORDER = ["Intern", "Junior", "Middle", "Senior", "Lead"]


@dataclass
class Posting:
    """One vacancy as the listing API describes it — no page fetch needed."""

    id: str
    url: str
    title: str
    company: str
    company_alias: str = ""
    qualification: str = ""
    # None: the source does not say. Only employer boards produce it; Habr and
    # Telegram always state a value.
    remote: bool | None = False
    location: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    salary_text: str = ""
    currency: str = ""
    divisions: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    published: str = ""
    employment: str = ""
    # Russian IT accreditation (Минцифры) as the source states it. None: the
    # source does not say — Telegram, geekjob, most employer boards. Habr's
    # False means "not confirmed on Habr", not "absent from the registry":
    # Yandex, accredited by the ministry, carried False there on 2026-10-01.
    accredited: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "company": self.company,
            "qualification": self.qualification,
            "remote": self.remote,
            "location": self.location,
            "salary_from": self.salary_from,
            "salary_to": self.salary_to,
            "salary_text": self.salary_text,
            "divisions": self.divisions,
            "skills": self.skills,
            "published": self.published,
            "accredited": self.accredited,
        }


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA, "Accept": "application/json", "Accept-Language": "ru"},
        timeout=30.0,
    )


def _posting(raw: dict) -> Posting:
    company = raw.get("company") or {}
    salary = raw.get("salary") or {}
    pub = raw.get("publishedDate") or {}
    return Posting(
        id=str(raw.get("id", "")),
        url=BASE + (raw.get("href") or ""),
        title=(raw.get("title") or "").strip(),
        company=(company.get("title") or "").strip(),
        company_alias=company.get("alias_name") or "",
        qualification=raw.get("qualification") or "",
        remote=bool(raw.get("remoteWork")),
        location=(raw.get("location") or {}).get("title", "") if raw.get("location") else "",
        salary_from=salary.get("from"),
        salary_to=salary.get("to"),
        salary_text=salary.get("formatted") or "",
        currency=salary.get("currency") or "",
        divisions=[d.get("title", "") for d in (raw.get("divisions") or [])],
        skills=[s.get("title", "") for s in (raw.get("skills") or [])],
        published=(pub.get("date") or ""),
        employment=raw.get("employment") or "",
        accredited=company.get("accredited"),
    )


def fetch_page(
    query: str = "",
    page: int = 1,
    remote: bool = False,
    qualifications: list[str] | None = None,
    salary_from: int | None = None,
    client: httpx.Client | None = None,
) -> tuple[list[Posting], dict]:
    """One page of the listing. Returns (postings, meta)."""
    params: list[tuple[str, str]] = [("type", "all"), ("page", str(page))]
    if query:
        params.append(("q", query))
    if remote:
        params.append(("remote", "true"))
    for name in qualifications or []:
        qid = QUALIFICATION.get(name.lower())
        if qid:
            params.append(("qid[]", str(qid)))
    if salary_from:
        params.append(("salary", str(salary_from)))

    own = client is None
    client = client or _client()
    try:
        r = client.get(f"{API}?{urlencode(params)}")
        r.raise_for_status()
        data = r.json()
    finally:
        if own:
            client.close()

    return [_posting(x) for x in data.get("list", [])], data.get("meta", {})


def search(
    query: str = "",
    pages: int = 1,
    remote: bool = False,
    qualifications: list[str] | None = None,
    salary_from: int | None = None,
    client: httpx.Client | None = None,
) -> list[Posting]:
    """Walk up to `pages` pages of one query."""
    out: list[Posting] = []
    own = client is None
    client = client or _client()
    try:
        for page in range(1, pages + 1):
            items, meta = fetch_page(
                query=query, page=page, remote=remote,
                qualifications=qualifications, salary_from=salary_from, client=client,
            )
            out.extend(items)
            if page >= int(meta.get("totalPages") or 1):
                break
            if page < pages:
                time.sleep(DELAY_SECONDS)
    finally:
        if own:
            client.close()
    return out


def search_many(
    queries: list[str],
    pages: int = 1,
    remote: bool = False,
    qualifications: list[str] | None = None,
    salary_from: int | None = None,
) -> list[Posting]:
    """Run several queries and merge them, keeping the first sighting of each id."""
    seen: set[str] = set()
    merged: list[Posting] = []
    with _client() as client:
        for i, q in enumerate(queries):
            for p in search(
                query=q, pages=pages, remote=remote, qualifications=qualifications,
                salary_from=salary_from, client=client,
            ):
                if p.id in seen:
                    continue
                seen.add(p.id)
                merged.append(p)
            if i < len(queries) - 1:
                time.sleep(DELAY_SECONDS)
    return merged
