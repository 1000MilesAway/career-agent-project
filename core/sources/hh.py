"""hh.ru through the hh-scrapper provider, a separate service on another host.

Why a provider and not a direct read (docs/decisions.md, 2026-10-03):
anonymous `api.hh.ru` answers 403 from everywhere, and hh answers 403 to this
server's HTML requests too. From the provider's host (185.153.180.173) the
HTML is served, so the provider reads hh's pages there and hands back JSON.
Its code lives in its own repository (github.com/1000MilesAway/hh-scrapper).

What a search returns per card: title, company, area, monthly band, work
formats, experience, and hh's snippet (requirements, duties, key skills). No
description: that is one more page per vacancy, read only when a user asks
for an evaluation (`fetch_vacancy`), not during the scan — the free filters
are about to drop most of the cards.

The provider reports trouble as statuses, never as an empty list: 503 is hh
showing a captcha, 502 an hh outage or a parser out of date. All of them
raise here, so a scan says "hh: ошибка" instead of "hh: 0 вакансий".

Nothing here touches the database; configuration comes from the environment.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .habr_api import Posting

SOURCE = "hh"
# Russia. Without an area hh mixes in Kazakhstan, Belarus and Uzbekistan, and
# a band in tenge reads as rubles to anyone who skims the currency.
AREA_RUSSIA = 113
# One provider request per query: a page of 100, hh's largest.
PAGES = 1
# Relevance over the last few days rather than the newest first: the listing
# is read once a day (pool.FRESH_FOR), and the newest 100 of a broad query
# like "python" cover only hours of hh's daily volume.
ORDER_BY = "relevance"
SEARCH_PERIOD_DAYS = 3

_VACANCY_URL = re.compile(r"^https://(?:[a-z0-9-]+\.)?hh\.ru/vacancy/(\d{5,12})(?:[/?#]|$)")

EXPERIENCE = {
    "noExperience": "без опыта",
    "between1And3": "опыт 1–3 года",
    "between3And6": "опыт 3–6 лет",
    "moreThan6": "опыт более 6 лет",
}
_CURRENCY_SIGN = {"RUB": "₽", "USD": "$", "EUR": "€"}


class NotConfigured(RuntimeError):
    """HH_PROVIDER_URL or HH_PROVIDER_TOKEN is not set."""


class ProviderError(RuntimeError):
    """The provider answered with an error: hh blocked it, hh is down, or it is."""


def _config() -> tuple[str, str]:
    url = os.getenv("HH_PROVIDER_URL", "").strip().rstrip("/")
    token = os.getenv("HH_PROVIDER_TOKEN", "").strip()
    if not url or not token:
        raise NotConfigured("источник hh.ru не настроен: нет HH_PROVIDER_URL или HH_PROVIDER_TOKEN")
    return url, token


def configured() -> bool:
    try:
        _config()
    except NotConfigured:
        return False
    return True


def vacancy_id(url: str) -> str | None:
    m = _VACANCY_URL.match(url.strip())
    return m.group(1) if m else None


def is_hh_url(url: str) -> bool:
    return vacancy_id(url) is not None


def _get(path: str, params: Any = None, timeout: float = 120.0) -> dict[str, Any]:
    base, token = _config()
    try:
        r = httpx.get(
            f"{base}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            # A search the provider has not cached waits for hh, paced at a
            # request every couple of seconds behind whatever else is queued.
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
    except httpx.HTTPError as e:
        raise ProviderError(f"провайдер hh.ru не ответил: {type(e).__name__}") from e
    if r.status_code == 200:
        return r.json()
    try:
        body = r.json()
    except ValueError:
        body = {}
    kind = body.get("error") or body.get("detail") or r.text[:120]
    if r.status_code == 503:
        raise ProviderError(f"hh.ru временно не пускает провайдер (капча или блокировка): {kind}")
    raise ProviderError(f"провайдер hh.ru ответил {r.status_code}: {kind}")


def _salary_text(v: dict[str, Any]) -> str:
    """The band as a person writes it, in the unit hh stated it in."""
    salary = v.get("salary") or {}
    low, high = salary.get("salary_from"), salary.get("salary_to")
    if not low and not high:
        return ""
    sign = _CURRENCY_SIGN.get(salary.get("currency") or "", salary.get("currency") or "")
    if low and high and low != high:
        text = f"{low:,}–{high:,}"
    elif low and high:
        text = f"{low:,}"
    elif low:
        text = f"от {low:,}"
    else:
        text = f"до {high:,}"
    text = f"{text} {sign}".replace(",", " ").strip()
    unit = {"HOUR": "в час", "SHIFT": "за смену", "FLY_IN_FLY_OUT": "за вахту"}.get(
        salary.get("mode") or "MONTH"
    )
    return f"{text} {unit}" if unit else text


def as_posting(v: dict[str, Any]) -> Posting:
    """A provider card in the shape the free filters read.

    `qualification` stays empty: hh states years of experience, not a grade,
    and guessing "Middle" from "3–6 лет" would make the grade filter drop
    postings on a number hh never published. The experience goes to
    `employment`, where the user and triage see it.
    """
    experience = EXPERIENCE.get(v.get("experience") or "", "")
    employment = ", ".join(x for x in (experience,) if x)
    return Posting(
        id=str(v["id"]),
        url=v["url"],
        title=v["title"],
        company=v.get("company") or "",
        remote=bool(v.get("remote")),
        location=v.get("area") or "",
        salary_from=v.get("salary_from"),
        salary_to=v.get("salary_to"),
        salary_text=_salary_text(v),
        currency=v.get("currency") or "",
        skills=list(v.get("skills") or []),
        published=v.get("published") or "",
        employment=employment,
    )


def search(query: str, remote: bool = False, pages: int = PAGES) -> tuple[list[Posting], bool]:
    """One query's cards, and whether hh had more than were read."""
    params: list[tuple[str, Any]] = [
        ("text", query),
        ("area", AREA_RUSSIA),
        ("order_by", ORDER_BY),
        ("search_period", SEARCH_PERIOD_DAYS),
        ("pages", pages),
    ]
    if remote:
        params.append(("work_format", "REMOTE"))
    data = _get("/search", params)
    return [as_posting(v) for v in data.get("vacancies") or []], bool(data.get("truncated"))


def as_text(v: dict[str, Any]) -> str:
    """What the model reads: the fields first, then the description."""
    lines = [v["title"]]
    if v.get("company"):
        lines.append(f"Компания: {v['company']}")
    if v.get("area"):
        lines.append(f"Локация: {v['area']}")
    if v.get("work_formats"):
        formats = {"REMOTE": "удалённо", "HYBRID": "гибрид", "ON_SITE": "офис",
                   "FIELD_WORK": "разъездная"}
        lines.append("Формат: " + ", ".join(formats.get(f, f) for f in v["work_formats"]))
    if salary := _salary_text(v):
        lines.append(f"Зарплата: {salary}")
    if v.get("experience_text"):
        lines.append(f"Опыт: {v['experience_text']}")
    if v.get("skills"):
        lines.append("Ключевые навыки: " + ", ".join(v["skills"]))
    if v.get("description"):
        lines += ["", v["description"]]
    return "\n".join(lines)


def fetch_vacancy(url: str) -> tuple[str, dict[str, Any]]:
    """Full text of one hh vacancy, for evaluation. Raises when there is none."""
    vid = vacancy_id(url)
    if vid is None:
        raise ValueError(f"не ссылка на вакансию hh.ru: {url}")
    data = _get(f"/vacancy/{vid}", timeout=60.0)
    status = data.get("status")
    if status == "not_found":
        raise ValueError("вакансия на hh.ru не найдена — её удалили")
    if status == "restricted":
        raise ValueError("hh.ru показывает эту вакансию только избранным соискателям")
    v = data["vacancy"]
    return as_text(v), {"url": v["url"], "title": v["title"], "archived": v.get("archived")}
