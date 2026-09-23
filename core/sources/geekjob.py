"""geekjob.ru — a Russian IT job board, read from its server-rendered listing.

Ported from career-ops (`providers/geekjob.mjs`), which settled the two
questions that decide how this can be read at all (re-checked 2026-09-23):

  - robots.txt allows `/` and disallows only `/json/` and `/rest/`. The site's
    own search box posts to `/json/find/vacancy`, so keyword filtering is
    deliberately NOT done at the source: we page through the newest listings
    and PreScreen filters them, as it does for Telegram.
  - `sitemap.xml` is frozen at 2020, so the listing is the only live path.

A page holds 20 cards, about two days of postings; the board runs ~12 pages.

The card carries title, company, city, salary as text ("от 210K ₽"), a remote
label and a day without a year. The full description is on the vacancy page as
schema.org JobPosting and is read on demand (core/sources/jobposting.py), not
during the scan — a scan that fetched every page would be twenty requests per
page of listings, for postings the free filters are about to drop.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, date, datetime
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from . import jobposting, salary_text
from .habr_api import UA, Posting

ORIGIN = "https://geekjob.ru"
HOST = "geekjob.ru"
DELAY_SECONDS = 0.5
MAX_PAGES = 15

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
VACANCY_PATH_RE = re.compile(r"^/vacancy/([a-f0-9]{12,32})$")


class MarkupChanged(RuntimeError):
    """The page has vacancy cards, but none parsed.

    Loud on purpose: otherwise a redesign reads as "no jobs today", which is
    indistinguishable from a quiet board.
    """


def is_geekjob_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == HOST


def list_url(page: int) -> str:
    return f"{ORIGIN}/vacancies" if page <= 1 else f"{ORIGIN}/vacancies/{int(page)}"


def _published(day_month: str, today: date) -> str:
    """ISO date for "22 сентября". The card states no year.

    A month later than the current one belongs to last year: the listing is
    newest-first and a few weeks deep, so in January "28 декабря" is last
    December, not a date eleven months ahead.
    """
    m = re.match(r"(\d{1,2})\s+([а-я]+)", day_month.strip().lower())
    if not m or m.group(2) not in MONTHS:
        return ""
    month, day = MONTHS[m.group(2)], int(m.group(1))
    year = today.year - 1 if month > today.month else today.year
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def parse_list(html: str, today: date | None = None) -> list[Posting]:
    """Cards of one listing page. Raises MarkupChanged on cards that do not parse."""
    today = today or datetime.now(UTC).date()
    tree = HTMLParser(html)
    cards = tree.css("li.collection-item.avatar")
    out: list[Posting] = []

    for card in cards:
        link = card.css_first("a.title")
        if link is None:
            continue
        href = link.attributes.get("href") or ""
        # The path comes from someone else's markup: re-check host and shape
        # rather than trusting it to point back at the board.
        url = urljoin(ORIGIN, href)
        m = VACANCY_PATH_RE.match(urlparse(url).path)
        if not is_geekjob_url(url) or not m:
            continue
        title = link.text(strip=True)
        if not title:
            continue

        company_node = card.css_first("p.company-name a")
        salary_node = card.css_first("span.salary")
        time_node = card.css_first("time")
        # First .info holds "City<br><span class=salary>"; the city is its
        # text before the salary span.
        info = card.css_first("div.info a")
        city = ""
        if info is not None:
            city = (info.text(deep=False, strip=True) or "").strip()

        salary = salary_node.text(strip=True) if salary_node else ""
        band = salary_text.parse_salary(salary) if salary else None
        # Only a rouble band feeds PreScreen: `salary_min` is a bare number
        # read as roubles, so "4.5K $" handed over as 4500 would drop a
        # well-paid job. Same rule as the Telegram adapter.
        rub = band is not None and band.currency == "RUB"

        out.append(
            Posting(
                id=m.group(1),
                url=url,
                title=title,
                company=company_node.text(strip=True) if company_node else "",
                remote=card.css_first("span.remote-label") is not None,
                location=city,
                salary_from=band.min if rub else None,
                salary_to=band.max if rub else None,
                salary_text=salary,
                currency=band.currency if band else "",
                published=_published(time_node.text(strip=True), today) if time_node else "",
                employment="part_time" if card.css_first("span.parttime-label") else "",
            )
        )

    if cards and not out:
        raise MarkupChanged("geekjob: на странице есть карточки, но ни одна не разобрана")
    return out


def _client() -> httpx.Client:
    # No redirects: the listing never redirects, and a redirect off the host
    # is exactly what the URL checks above refuse to follow.
    return httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ru"},
        timeout=30.0,
        follow_redirects=False,
    )


def fetch_vacancy(url: str, client: httpx.Client | None = None) -> jobposting.JobPosting:
    """The full vacancy from its page's JSON-LD.

    Raises ValueError when the page carries no posting, or is archived: an
    archived geekjob page still serves the old JSON-LD, and scoring it would be
    paying for a vacancy nobody can apply to.
    """
    if not is_geekjob_url(url):
        raise ValueError(f"не ссылка на geekjob: {url}")
    own = client is None
    client = client or _client()
    try:
        r = client.get(url)
        r.raise_for_status()
    finally:
        if own:
            client.close()
    if "Архивная вакансия" in r.text:
        raise ValueError("вакансия на geekjob перемещена в архив")
    posting = jobposting.parse(r.text)
    if posting is None:
        raise ValueError("на странице geekjob нет вакансии в понятном формате")
    return posting


def collect(pages: int = 5, client: httpx.Client | None = None) -> list[Posting]:
    """The newest `pages` listing pages, deduplicated by id.

    Stops early on an empty page or a page of nothing but repeats (the board
    looping back on itself past its last page).
    """
    pages = max(1, min(pages, MAX_PAGES))
    seen: set[str] = set()
    out: list[Posting] = []
    own = client is None
    client = client or _client()
    try:
        for page in range(1, pages + 1):
            if page > 1:
                time.sleep(DELAY_SECONDS)
            r = client.get(list_url(page))
            if r.status_code == 404:
                break
            r.raise_for_status()
            rows = parse_list(r.text)
            fresh = [p for p in rows if p.id not in seen]
            if not fresh:
                break
            for p in fresh:
                seen.add(p.id)
            out.extend(fresh)
    finally:
        if own:
            client.close()
    return out
