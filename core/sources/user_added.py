"""A vacancy the user brought: a link to its page, or its text pasted in.

The spec's main scenario — "here is a vacancy, is it worth it?" — for postings
no scan found. Both ways end in the same `Posting` the free filters read and
the same plain text the evaluation reads, so nothing downstream needs to know
where the vacancy came from.

A link is read by the source's own reader when the site is one we scan (Habr,
hh, geekjob): the vacancy then lands in the shared pool under that source and
id, and is the same posting a scan would find. Any other page is read by its
schema.org JobPosting (core/sources/jobposting.py) — the markup career sites
keep for Google for Jobs. A page without one is refused with a request to
paste the text: scraping a stranger's visible markup would hand the model a
menu, a cookie banner and a footer as "the vacancy".

Pasted text is the user's own, possibly with things they would not publish.
Its posting id is prefixed with the owner's id, so it is never the same row
as anyone else's, and the app keeps it out of every other user's scan.

Nothing here touches the database; the network is only in `from_url`.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from .. import tg_scan
from . import geekjob, habr, hh, jobposting, salary_text, telegram
from .habr_api import UA, Posting

# Sources of postings that exist only because a user brought them.
TEXT_SOURCE = "text"
PAGE_SOURCE = "url"

# A vacancy shorter than this is a title, not a description: the model would
# be paid to judge one line.
MIN_TEXT_CHARS = 80
# Long enough for any real posting with its benefits list; a pasted page with
# its navigation and comments is not a vacancy, and the evaluation would pay
# for every character of it.
MAX_TEXT_CHARS = 20_000
TITLE_CHARS = 160

# Reading a stranger's page: bounded in size, hops and time.
MAX_PAGE_BYTES = 3_000_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20.0

COMPANY_RE = re.compile(
    r"^\s*(?:компания|работодатель|company)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
LINK_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

NO_POSTING_ON_PAGE = (
    "На странице не нашлось описания вакансии в машиночитаемом виде. "
    "Скопируйте текст вакансии и вставьте его сюда."
)


@dataclass
class Added:
    """One vacancy the user brought, ready to be pooled."""

    source: str
    posting: Posting
    # What the evaluation reads: the source's fields, then the description.
    text: str
    # Set when the page itself says the vacancy is closed (an archived hh
    # card, a JobPosting past its validThrough). Added all the same — the
    # user asked about it — but marked, so nobody pays to evaluate it.
    closed_reason: str | None = None


def is_link(raw: str) -> bool:
    """A single URL and nothing else. A link inside a pasted text is text."""
    return bool(LINK_RE.match(raw.strip()))


def _normalize_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def text_digest(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode()).hexdigest()[:32]


def from_text(raw: str, owner: str) -> Added:
    """A pasted vacancy, read by the same free rules as a Telegram post.

    No model: the title is the first line with words in it, the grade, stack,
    company and salary are read only where the text labels them. What is not
    stated stays empty — the free filters pass a posting with no data rather
    than guess (core/filters.py), and the evaluation reads the full text.
    """
    text = _normalize_text(raw)
    if len(text) < MIN_TEXT_CHARS:
        raise ValueError(
            "Слишком короткий текст — вставьте вакансию целиком: обязанности, требования, условия."
        )
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(
            f"Слишком длинный текст: больше {MAX_TEXT_CHARS:,} знаков. ".replace(",", " ")
            + "Оставьте только саму вакансию."
        )

    grade = telegram.GRADE_RE.search(text)
    stack = telegram.STACK_RE.search(text)
    company = COMPANY_RE.search(text)
    band = salary_text.parse_salary(text)
    post = telegram.Post(
        id=f"{owner}:{text_digest(text)}",
        url="",
        channel="",
        text=text,
        title=(telegram._first_title_line(text) or "Вакансия без заголовка")[:TITLE_CHARS],
        grade=grade.group(1).strip() if grade else "",
        stack=[s.strip() for s in stack.group(1).split(",") if s.strip()] if stack else [],
        remote=bool(telegram.REMOTE_RE.search(text)),
        has_salary=bool(telegram.SALARY_RE.search(text)),
        salary_from=band.min if band else None,
        salary_to=band.max if band else None,
        salary_currency=band.currency if band else "",
    )
    posting = tg_scan.as_posting(post)
    posting.url = ""
    posting.company = company.group(1).strip()[:255] if company else ""
    # Not mentioning remote work is not saying "office": unknown, not False.
    posting.remote = True if post.remote else None
    return Added(source=TEXT_SOURCE, posting=posting, text=text)


def from_url(url: str, client: httpx.Client | None = None) -> Added:
    """A vacancy by its page. Network; raises ValueError with the reason."""
    url = url.strip()
    if habr.is_habr_url(url):
        return _from_habr(url)
    if hh.is_hh_url(url):
        return _from_hh(url)
    if geekjob.is_geekjob_url(url):
        page = geekjob.fetch_vacancy(url)
        m = geekjob.VACANCY_PATH_RE.match(urlsplit(url).path.rstrip("/"))
        if m is None:
            raise ValueError("не ссылка на вакансию geekjob.ru")
        return _from_jobposting("geekjob", m.group(1), url.split("?")[0], page)
    if telegram_post(url):
        raise ValueError(
            "Пост из Telegram по ссылке пока не читаем. Скопируйте текст поста и вставьте его сюда."
        )

    own = client is None
    client = client or _client()
    try:
        final_url, html = fetch_page(url, client)
    finally:
        if own:
            client.close()
    page = jobposting.parse(html)
    if page is None or not page.description:
        raise ValueError(NO_POSTING_ON_PAGE)
    return _from_jobposting(PAGE_SOURCE, page_id(final_url), final_url, page)


def telegram_post(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in ("t.me", "telegram.me")


def page_id(url: str) -> str:
    """A stable id for a page: the same link pasted twice is one posting."""
    parts = urlsplit(url)
    key = f"{parts.netloc.lower()}{parts.path.rstrip('/')}?{parts.query}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _from_habr(url: str) -> Added:
    vac = habr.fetch_vacancy(url)
    band = salary_text.parse_salary(vac.salary)
    posting = Posting(
        id=vac.vacancy_id,
        url=vac.url,
        title=vac.title or "Вакансия на Хабр Карьере",
        company=vac.company,
        # The page does not state these the way the API does; None and empty
        # leave whatever a scan stored untouched.
        remote=None,
        location=vac.location,
        salary_from=band.min if band else None,
        salary_to=band.max if band else None,
        salary_text=vac.salary,
        currency=band.currency if band else "",
        skills=vac.skills,
    )
    return Added(source="habr", posting=posting, text=vac.as_text())


def _from_hh(url: str) -> Added:
    try:
        card = hh.fetch_card(url)
    except hh.NotConfigured as e:
        raise ValueError(
            "Ссылки на hh.ru сейчас не читаются. Скопируйте текст вакансии и вставьте его сюда."
        ) from e
    except hh.ProviderError as e:
        raise ValueError(f"hh.ru не ответил: {e}. Можно вставить текст вакансии.") from e
    closed = "вакансия в архиве hh.ru" if card.get("archived") else None
    return Added(source="hh", posting=hh.as_posting(card), text=hh.as_text(card),
                 closed_reason=closed)


def _from_jobposting(source: str, pid: str, url: str, page: jobposting.JobPosting) -> Added:
    rub = page.currency in ("RUB", "RUR")
    ends = [x for x in (page.salary_from, page.salary_to) if x]
    band = "–".join(f"{x:,}".replace(",", " ") for x in dict.fromkeys(ends))
    posting = Posting(
        id=pid,
        url=url,
        title=page.title[:512] or "Вакансия",
        company=page.company,
        remote=True if page.remote else None,
        location=page.location,
        salary_from=page.salary_from if rub else None,
        salary_to=page.salary_to if rub else None,
        salary_text=f"{band} {page.currency}".strip() if band else "",
        currency="RUB" if rub else page.currency,
        published=page.date_posted,
    )
    closed = "срок публикации вакансии истёк" if page.expired() else None
    return Added(source=source, posting=posting, text=page.as_text(), closed_reason=closed)


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ru"},
        timeout=TIMEOUT_SECONDS,
        # Followed by hand: every hop is checked against private addresses.
        follow_redirects=False,
    )


def _public(host: str) -> bool:
    """Every address the host resolves to is on the public internet.

    The server would otherwise fetch whatever a user names — the database
    port, the provider's API, a cloud metadata address — and hand the reply
    back as "the vacancy". Checked per redirect hop. The resolve and the
    connect are two lookups, so a host that changes its answer in between
    gets through; the size cap and the JobPosting requirement keep what such
    a fetch could return to a parsed posting.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as e:
        raise ValueError(f"Сайт {host} не найден — проверьте ссылку.") from e
    return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def fetch_page(url: str, client: httpx.Client) -> tuple[str, str]:
    """(final URL, HTML) of a public page, bounded in size and hops."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("Ссылка должна начинаться с http:// или https://")
        if parts.port not in (None, 80, 443) or not _public(parts.hostname):
            raise ValueError("Эту ссылку открыть нельзя: она ведёт не на публичный сайт.")
        try:
            with client.stream("GET", url) as r:
                if r.is_redirect and "location" in r.headers:
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code in (404, 410):
                    raise ValueError("Страница не найдена — возможно, вакансию сняли.")
                if r.status_code >= 400:
                    raise ValueError(f"Сайт ответил ошибкой {r.status_code}.")
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_PAGE_BYTES:
                        raise ValueError(NO_POSTING_ON_PAGE)
                return url, body.decode(r.encoding or "utf-8", errors="replace")
        except httpx.HTTPError as e:
            raise ValueError(f"Сайт не ответил ({type(e).__name__}). Можно вставить текст.") from e
    raise ValueError("Слишком много перенаправлений — откройте ссылку и скопируйте текст.")
