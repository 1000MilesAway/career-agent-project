"""Is a vacancy still open? Free: one HTTP request, no model.

The rule every branch follows, taken from career-ops' liveness-core: a false
"closed" is the expensive error. It hides a real job from the user, and a
closed row is never collected as new again. So only positive evidence closes a
vacancy — a 404/410, or the page's own archive banner, or a JobPosting whose
`validThrough` has passed. Anything else — 5xx, 429, a timeout, a captcha, a
page we do not recognise — is "unknown", and unknown is treated as open.

Verified 2026-09-23 on live pages:
  - career.habr.com answers 410 for some archived vacancies and 200 with
    "Вакансия в архиве" (and no description) for others;
  - geekjob.ru answers 200 for archived vacancies too, with a
    `.ui.warning.message` banner "Архивная вакансия" above the posting — and
    still serves the JobPosting JSON-LD, so the banner is the only signal.

Telegram posts have no page to ask: their state is always unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
from selectolax.parser import HTMLParser

from .sources import geekjob, jobposting
from .sources.habr import is_habr_url
from .sources.habr_api import UA

State = Literal["open", "closed", "unknown"]

HABR_ARCHIVE_MARK = "Вакансия в архиве"
GEEKJOB_ARCHIVE_MARK = "Архивная вакансия"


@dataclass(frozen=True)
class Liveness:
    state: State
    reason: str = ""

    @property
    def closed(self) -> bool:
        return self.state == "closed"


UNKNOWN_SOURCE = Liveness("unknown", "источник не умеет сообщать, открыта ли вакансия")


def can_check(url: str | None) -> bool:
    return bool(url) and (is_habr_url(url) or geekjob.is_geekjob_url(url))


def classify(url: str, status: int, html: str) -> Liveness:
    """The verdict for one fetched page. Split from `check` so tests need no network."""
    if status in (404, 410):
        return Liveness("closed", f"страница вакансии отвечает {status}")
    if status != 200:
        # 429, 5xx, a redirect to a login wall: none of them says anything
        # about the vacancy.
        return Liveness("unknown", f"страница ответила {status}")

    if is_habr_url(url):
        tree = HTMLParser(html)
        body = tree.body.text(separator=" ") if tree.body else ""
        if HABR_ARCHIVE_MARK in body:
            return Liveness("closed", "работодатель перенёс вакансию в архив")
        return Liveness("open")

    if geekjob.is_geekjob_url(url):
        tree = HTMLParser(html)
        for banner in tree.css(".ui.warning.message"):
            if GEEKJOB_ARCHIVE_MARK in banner.text():
                return Liveness("closed", "вакансия перемещена в архив")
        posting = jobposting.parse(html)
        if posting is None:
            # A page with neither a banner nor a posting is not one we know —
            # a redesign, an error page served as 200. Not evidence of closure.
            return Liveness("unknown", "на странице нет вакансии в понятном формате")
        if posting.expired():
            return Liveness("closed", f"срок публикации истёк {posting.valid_through[:10]}")
        return Liveness("open")

    return UNKNOWN_SOURCE


def client() -> httpx.Client:
    """A client for a run of checks; `check` makes its own when given none."""
    return httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ru"},
        timeout=20.0,
        follow_redirects=True,
    )


def check(url: str | None, http: httpx.Client | None = None) -> Liveness:
    """Fetch the vacancy page and classify it. Never raises."""
    if not url or not can_check(url):
        return UNKNOWN_SOURCE
    own = http is None
    http = http or client()
    try:
        r = http.get(url)
        # A redirect that lands off the source's host is not the vacancy.
        if not can_check(str(r.url)):
            return Liveness("unknown", "страница увела на другой сайт")
        return classify(str(r.url), r.status_code, r.text)
    except httpx.HTTPError as e:
        return Liveness("unknown", f"сеть: {type(e).__name__}")
    finally:
        if own:
            http.close()
