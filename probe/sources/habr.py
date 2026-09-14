"""Habr Career reader: one vacancy by URL, or a filtered search page.

Only public vacancy pages are touched. robots.txt allows /vacancies and the search
listing; it disallows /responses, /conversations and profile pages, which we never
request — the product does not apply on anyone's behalf.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode, urljoin

import httpx
from selectolax.parser import HTMLParser

BASE = "https://career.habr.com"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Be a polite guest: one request at a time, with a pause between them.
DELAY_SECONDS = 1.0

VACANCY_RE = re.compile(r"^https?://career\.habr\.com/vacancies/(\d+)")


@dataclass
class Vacancy:
    """A vacancy as read from its public page."""

    url: str
    vacancy_id: str
    title: str = ""
    company: str = ""
    salary: str = ""
    predicted_salary: str = ""
    location: str = ""
    level: str = ""
    posted: str = ""
    skills: list[str] = field(default_factory=list)
    description: str = ""

    def as_text(self) -> str:
        """Flatten to the plain text the LLM pipeline expects."""
        parts = [self.title]
        if self.company:
            parts.append(f"Компания: {self.company}")
        if self.location:
            parts.append(f"Локация: {self.location}")
        if self.level:
            parts.append(f"Уровень: {self.level}")
        parts.append(f"Зарплата: {self.salary or 'не указана'}")
        if self.predicted_salary:
            # Habr's own estimate. Mark it so the model does not read it as the offer.
            parts.append(f"(оценка калькулятора Хабра, не от работодателя: {self.predicted_salary})")
        if self.skills:
            parts.append("Навыки: " + ", ".join(self.skills))
        parts.append("")
        parts.append(self.description)
        return "\n".join(parts).strip()

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "id": self.vacancy_id,
            "title": self.title,
            "company": self.company,
            "salary": self.salary,
            "predicted_salary": self.predicted_salary,
            "location": self.location,
            "level": self.level,
            "posted": self.posted,
            "skills": self.skills,
        }


def is_habr_url(url: str) -> bool:
    return bool(VACANCY_RE.match(url.strip()))


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.9"},
        timeout=30.0,
        follow_redirects=True,
    )


def _text(node) -> str:
    return node.text(strip=True) if node else ""


def _split_salary(node) -> tuple[str, str]:
    """Separate the employer's figure from Habr's own calculator estimate."""
    if not node:
        return "", ""
    stated = _text(node.css_first(".predicted-salary__title"))
    predicted = ""
    for part in node.css(".predicted-salary__value, .basic-salary"):
        t = _text(part)
        if t and t != stated:
            predicted = t
            break
    if not predicted:
        whole = node.text(separator="|", strip=True)
        pieces = [p.strip() for p in whole.split("|") if p.strip()]
        rest = [p for p in pieces if p != stated]
        predicted = rest[0] if rest else ""
    if "не указана" in stated.lower():
        return "", predicted
    return stated, predicted


def _company_name(node) -> str:
    """Company cards append a rating (e.g. "IBS4.02"); keep just the name."""
    if not node:
        return ""
    link = node.css_first(".vacancy-card__company-title, a")
    name = _text(link) if link else _text(node)
    return re.sub(r"\d[\d.,]*$", "", name).strip()


CITY_RE = re.compile(r"в городах? ([^,]+(?:, [^,]+)*?) ?(?:и ([^,]+?))?, работа")


def _location_from_title(tree) -> str:
    """The <title> is the only place a vacancy page states its city."""
    node = tree.css_first("title")
    if not node:
        return ""
    m = CITY_RE.search(_text(node))
    if not m:
        return ""
    cities = [m.group(1)]
    if m.group(2):
        cities.append(m.group(2))
    return ", ".join(c.strip() for c in cities if c)


def parse_vacancy_html(html: str, url: str) -> Vacancy:
    """Extract the fields we need from a vacancy page."""
    tree = HTMLParser(html)
    m = VACANCY_RE.match(url)
    vac = Vacancy(url=url, vacancy_id=m.group(1) if m else "")

    # The h1 is nested; taking the header div wholesale also swallows the date.
    vac.title = _text(tree.css_first(".page-title__title"))
    vac.company = _text(tree.css_first(".vacancy-company__title"))
    vac.posted = _text(tree.css_first(".vacancy-header__date"))

    # Habr shows either a real range from the employer, or its own calculator estimate.
    # They mean different things, so keep them apart.
    vac.salary, vac.predicted_salary = _split_salary(tree.css_first(".vacancy-header__salary"))

    # Skills and city are not on the vacancy page itself — the title tag carries the city.
    vac.location = _location_from_title(tree)

    body = tree.css_first(".vacancy-description__text") or tree.css_first(".style-ugc")
    if body:
        for bad in body.css("script, style"):
            bad.decompose()
        text = body.text(separator="\n", strip=True)
        vac.description = re.sub(r"\n{3,}", "\n\n", text)

    if not vac.title:
        title_tag = tree.css_first("title")
        if title_tag:
            vac.title = _text(title_tag).split("«")[1].split("»")[0]

    return vac


def fetch_vacancy(url: str, client: httpx.Client | None = None) -> Vacancy:
    """Read one vacancy by its public URL."""
    url = url.strip().split("?")[0]
    if not is_habr_url(url):
        raise ValueError(f"не ссылка на вакансию Хабр Карьеры: {url}")

    own = client is None
    client = client or _client()
    try:
        r = client.get(url)
        if r.status_code == 404:
            raise ValueError("вакансия не найдена — возможно, её сняли")
        r.raise_for_status()
        vac = parse_vacancy_html(r.text, url)
    finally:
        if own:
            client.close()

    if not vac.description:
        raise ValueError("не удалось прочитать описание вакансии")
    return vac


def search(
    query: str = "",
    pages: int = 1,
    remote: bool = False,
    salary: int | None = None,
    specialization: str = "",
    sort: str = "relevance",
) -> list[dict]:
    """Search the public vacancy listing. Returns link cards, not full pages."""
    found: list[dict] = []
    seen: set[str] = set()

    with _client() as client:
        for page in range(1, pages + 1):
            params: dict[str, str | int] = {"type": "all", "page": page, "sort": sort}
            if query:
                params["q"] = query
            if remote:
                params["remote"] = 1
            if salary:
                params["salary"] = salary
            if specialization:
                params["s[]"] = specialization

            url = f"{BASE}/vacancies?{urlencode(params, doseq=True)}"
            r = client.get(url)
            r.raise_for_status()
            tree = HTMLParser(r.text)

            cards = tree.css(".vacancy-card")
            if not cards:
                break

            for card in cards:
                link = card.css_first(".vacancy-card__title-link")
                if not link:
                    continue
                href = link.attributes.get("href", "")
                full = urljoin(BASE, href.split("?")[0])
                if full in seen:
                    continue
                seen.add(full)
                stated, predicted = _split_salary(card.css_first(".vacancy-card__salary"))
                meta_node = card.css_first(".vacancy-card__meta")
                # Meta chips run together without separators; split them apart.
                meta_parts = (
                    [_text(x) for x in meta_node.css(".basic-chip__text, .chip-with-icon__text")]
                    if meta_node
                    else []
                )
                meta_parts = [x for x in meta_parts if x]
                found.append({
                    "url": full,
                    "title": _text(link),
                    "company": _company_name(card.css_first(".vacancy-card__company")),
                    "salary": stated,
                    "predicted_salary": predicted,
                    "meta": ", ".join(meta_parts) or _text(meta_node),
                    "skills": [
                        _text(x) for x in card.css(".vacancy-card__skills a") if _text(x)
                    ],
                    "date": _text(card.css_first(".vacancy-card__date")),
                })

            if page < pages:
                time.sleep(DELAY_SECONDS)

    return found


def fetch_many(urls: list[str]) -> list[Vacancy | Exception]:
    """Read several vacancies, pausing between requests."""
    out: list[Vacancy | Exception] = []
    with _client() as client:
        for i, u in enumerate(urls):
            try:
                out.append(fetch_vacancy(u, client=client))
            except Exception as e:
                out.append(e)
            if i < len(urls) - 1:
                time.sleep(DELAY_SECONDS)
    return out
