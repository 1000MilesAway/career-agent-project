"""Vacancies straight from employers' career sites and their ATS feeds.

The idea is Hirify's (docs/decisions.md, 2026-09-23): most of their volume is
not a job board at all, it is companies' own listings. Every endpoint here is
the one the company's own career page calls, public and unauthenticated, and
was checked live on 2026-09-23 together with its robots.txt. Sites behind an
antibot wall (Ozon, Alfa, T-Bank) and sites whose robots.txt closes the API
(Magnit, Lamoda, Kaspersky, 2GIS query URLs) are deliberately absent — the
list and the reasons are in the decision record, so nobody re-tries them.

Which companies are read is data (data/sources/employers.json), not code:
boards go stale on their own schedule, like Telegram channels.

Two kinds of board:
  - searchable (Sber, Yandex, VK): asked once per profile query;
  - small feeds (Huntflow, Greenhouse, Lever, Ashby): read whole — a company
    has tens of openings, not thousands — and PreScreen does the filtering.

Descriptions: Sber and the international ATS put the full text in the list,
so it is kept at collect time. Yandex, VK and Huntflow need a second request
per vacancy; `describe()` makes it, and the caller runs it only over what the
free filters kept — the same "expensive last" rule, applied to someone else's
bandwidth.

`remote` is three-valued here: True, False, or None when the board does not
say. PreScreen drops only an explicit False under "only remote", so a board
that publishes no work mode is not punished for the silence.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from selectolax.parser import HTMLParser

from . import jobposting, salary_text
from .habr_api import UA, Posting

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "sources" / "employers.json"

DELAY_SECONDS = 1.0
# Searchable boards: pages per query. Sber and Yandex rank by relevance, so
# the first pages are the ones worth reading.
PAGES_PER_QUERY = 2
# Descriptions fetched per scan, over the survivors only.
MAX_DESCRIBED = 30
MAX_TEXT_CHARS = 8000

REMOTE_RE = re.compile(r"удал[её]нн|удал[её]нк|remote|дистанцион", re.IGNORECASE)


@dataclass
class Board:
    kind: str
    name: str
    slug: str = ""
    group: str = "ru"
    # Stated in the list only where Habr shows the accreditation badge for
    # the same company. Never False: Habr's missing badge is no evidence
    # (Yandex lacks it), and False would drop the whole board under the filter.
    accredited: bool | None = None


@dataclass
class Collected:
    postings: list[Posting] = field(default_factory=list)
    # Texts known at collect time, by posting id.
    texts: dict[str, str] = field(default_factory=dict)
    # Boards that failed. Reported, never swallowed: a scan that quietly
    # returns less is indistinguishable from a quiet market.
    problems: list[dict[str, str]] = field(default_factory=list)


def load(path: Path | None = None) -> list[Board]:
    path = path or DEFAULT_PATH
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Board(
            kind=b["kind"],
            name=b["name"],
            slug=b.get("slug", ""),
            group=b.get("group", "ru"),
            accredited=b.get("accredited"),
        )
        for b in data.get("boards", [])
    ]


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA, "Accept": "application/json", "Accept-Language": "ru"},
        timeout=30.0,
        follow_redirects=True,
    )


def _html_text(fragment: str | None) -> str:
    return jobposting._html_to_text(fragment or "")


def _join(*parts: tuple[str, str | None]) -> str:
    """Sections as the model reads them: a heading, then the text."""
    out = []
    for heading, body in parts:
        body = (body or "").strip()
        if body:
            out.append(f"{heading}\n{body}" if heading else body)
    return "\n\n".join(out)[:MAX_TEXT_CHARS]


def _head(title: str, company: str, location: str) -> str:
    lines = [title, f"Компания: {company}"]
    if location:
        lines.append(f"Локация: {location}")
    return "\n".join(lines)


def _pid(board: Board, native: Any) -> str:
    # Unique across boards: two ATS number their postings independently.
    return f"{board.kind}/{board.slug or board.kind}/{native}"


# --- Sber -------------------------------------------------------------------

SBER_API = (
    "https://rabota.sber.ru/public/app-candidate-public-api-gateway/api/v1/publications"
)
SBER_TAKE = 50


def sber_posting(board: Board, raw: dict) -> tuple[Posting, str]:
    title = (raw.get("title") or "").strip()
    company = (raw.get("company") or board.name).strip()
    city = raw.get("city") or ""
    body = _join(
        ("", raw.get("introduction")),
        ("Обязанности:", raw.get("duties")),
        ("Требования:", raw.get("requirements")),
        ("Условия:", raw.get("conditions")),
    )
    low, high = raw.get("salary_min"), raw.get("salary_max")
    salary = "–".join(str(x) for x in dict.fromkeys(x for x in (low, high) if x))
    posting = Posting(
        id=_pid(board, raw.get("internalId")),
        url=f"https://rabota.sber.ru/search/{raw.get('internalId')}/",
        title=title,
        company=company,
        location=city,
        remote=True if REMOTE_RE.search(body) else None,
        salary_from=low or None,
        salary_to=high or None,
        salary_text=f"{salary} ₽" if salary else "",
        currency="RUB" if salary else "",
        published=(raw.get("publicationDate") or "")[:10],
    )
    text = _join(("", _head(title, company, city)), ("", salary and f"Зарплата: {salary} ₽"),
                 ("", body))
    return posting, text


def fetch_sber(board: Board, queries: list[str], client: httpx.Client, out: Collected) -> None:
    for query in queries:
        for page in range(PAGES_PER_QUERY):
            r = client.get(
                SBER_API,
                params={"skip": page * SBER_TAKE, "take": SBER_TAKE, "searchString": query},
            )
            r.raise_for_status()
            items = (r.json().get("data") or {}).get("vacancies") or []
            for raw in items:
                posting, text = sber_posting(board, raw)
                out.postings.append(posting)
                out.texts[posting.id] = text
            if len(items) < SBER_TAKE:
                break
            time.sleep(DELAY_SECONDS)
        time.sleep(DELAY_SECONDS)


# --- Yandex -----------------------------------------------------------------

YANDEX_API = "https://yandex.ru/jobs/api/publications"


def _yandex_remote(vacancy: dict) -> bool | None:
    modes = {m.get("slug") for m in (vacancy.get("work_modes") or [])}
    if not modes:
        return None
    return "remote" in modes


def yandex_posting(board: Board, raw: dict) -> Posting:
    vacancy = raw.get("vacancy") or {}
    cities = ", ".join(c.get("name", "") for c in vacancy.get("cities") or [])
    return Posting(
        id=_pid(board, raw.get("id")),
        url=f"https://yandex.ru/jobs/vacancies/{raw.get('publication_slug_url')}",
        title=(raw.get("title") or "").strip(),
        company=board.name,
        location=cities,
        remote=_yandex_remote(vacancy),
        skills=[s.get("name", "") for s in vacancy.get("skills") or []],
    )


def _cursor(next_url: str | None) -> str | None:
    """Only the cursor of `next`: the link itself points at an internal host."""
    m = re.search(r"[?&]cursor=([^&]+)", next_url or "")
    return m.group(1) if m else None


def fetch_yandex(board: Board, queries: list[str], client: httpx.Client, out: Collected) -> None:
    for query in queries:
        cursor = None
        for _ in range(PAGES_PER_QUERY):
            url = f"{YANDEX_API}?text={quote(query)}"
            if cursor:
                url += f"&cursor={cursor}"
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
            out.postings += [yandex_posting(board, raw) for raw in data.get("results") or []]
            cursor = _cursor(data.get("next"))
            if not cursor:
                break
            time.sleep(DELAY_SECONDS)
        time.sleep(DELAY_SECONDS)


def describe_yandex(native: str, client: httpx.Client) -> str:
    r = client.get(f"{YANDEX_API}/{native}")
    r.raise_for_status()
    d = r.json()
    cities = ", ".join(c.get("name", "") for c in (d.get("vacancy") or {}).get("cities") or [])
    return _join(
        ("", _head(d.get("title") or "", "Яндекс", cities)),
        ("", d.get("short_summary")),
        ("", d.get("description")),
        ("Обязанности:", d.get("duties")),
        ("Требования:", d.get("key_qualifications")),
        ("Будет плюсом:", d.get("additional_requirements")),
        ("Условия:", d.get("conditions")),
    )


# --- VK ---------------------------------------------------------------------

VK_API = "https://team.vk.company/career/api/v2/vacancies/"
VK_LIMIT = 50


def vk_posting(board: Board, raw: dict) -> Posting:
    group = (raw.get("group") or {}).get("name") or ""
    return Posting(
        id=_pid(board, raw.get("id")),
        url=f"https://team.vk.company/vacancy/{raw.get('id')}/",
        title=(raw.get("title") or "").strip(),
        company=f"{board.name} · {group}" if group else board.name,
        location=(raw.get("town") or {}).get("name") or "",
        remote=bool(raw.get("remote")),
        skills=[t.get("name", "") for t in raw.get("tags") or []],
    )


def fetch_vk(board: Board, queries: list[str], client: httpx.Client, out: Collected) -> None:
    for query in queries:
        for page in range(PAGES_PER_QUERY):
            r = client.get(
                VK_API, params={"limit": VK_LIMIT, "offset": page * VK_LIMIT, "title": query}
            )
            r.raise_for_status()
            data = r.json()
            out.postings += [vk_posting(board, raw) for raw in data.get("results") or []]
            if not data.get("next"):
                break
            time.sleep(DELAY_SECONDS)
        time.sleep(DELAY_SECONDS)


def vk_page_text(html: str) -> str:
    """The description from a server-rendered VK vacancy page.

    There is no JSON-LD (checked 2026-09-23), and the detail API returns the
    list fields only; the text lives in `.content`. Everything from "Похожие
    вакансии" on is other vacancies, and would be scored as this one.
    """
    node = HTMLParser(html).css_first(".content")
    if node is None:
        return ""
    text = node.text(separator="\n")
    text = text.split("Похожие вакансии", 1)[0]
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line and line != "Откликнуться")[:MAX_TEXT_CHARS]


def describe_vk(native: str, client: httpx.Client) -> str:
    r = client.get(f"https://team.vk.company/vacancy/{native}/", headers={"Accept": "text/html"})
    r.raise_for_status()
    return vk_page_text(r.text)


# --- Huntflow career sites --------------------------------------------------

HUNTFLOW_COUNT = 50  # 100 answers 404
HUNTFLOW_MAX_PAGES = 5


def _huntflow_base(board: Board) -> str:
    # The slug goes into a hostname: only a plain label is accepted, so a
    # hand-edited data file cannot aim the fetcher at another host.
    if not re.fullmatch(r"[a-z0-9-]{1,63}", board.slug):
        raise ValueError(f"некорректный slug Huntflow: {board.slug!r}")
    return f"https://{board.slug}.huntflow.io"


def huntflow_posting(board: Board, raw: dict) -> Posting:
    money = raw.get("money") or ""
    band = salary_text.parse_salary(money) if money else None
    rub = band is not None and band.currency == "RUB"
    city = raw.get("city") or ""
    return Posting(
        id=_pid(board, raw.get("slug") or raw.get("id")),
        url=f"{_huntflow_base(board)}/vacancy/{raw.get('slug')}",
        title=(raw.get("position") or "").strip(),
        company=board.name,
        location=city,
        remote=True if REMOTE_RE.search(city) else None,
        salary_from=band.min if rub else None,
        salary_to=band.max if rub else None,
        salary_text=money,
        currency=band.currency if band else "",
    )


def fetch_huntflow(board: Board, queries: list[str], client: httpx.Client, out: Collected) -> None:
    base = _huntflow_base(board)
    for page in range(1, HUNTFLOW_MAX_PAGES + 1):
        r = client.get(f"{base}/api/vacancy", params={"count": HUNTFLOW_COUNT, "page": page})
        r.raise_for_status()
        data = r.json()
        out.postings += [
            huntflow_posting(board, raw)
            for raw in data.get("items") or []
            if not raw.get("archived_at")
        ]
        # `total` is the number of PAGES, not of vacancies.
        if page >= int(data.get("total") or 1):
            break
        time.sleep(DELAY_SECONDS)


def describe_huntflow(board: Board, native: str, client: httpx.Client) -> str:
    r = client.get(f"{_huntflow_base(board)}/api/vacancy/{native}")
    r.raise_for_status()
    d = r.json()
    return _join(
        ("", _head(d.get("position") or "", board.name, d.get("city") or "")),
        ("", d.get("money") and f"Зарплата: {d['money']}"),
        ("", _html_text(d.get("intro"))),
        ("", _html_text(d.get("body"))),
        ("Требования:", _html_text(d.get("requirements"))),
        ("Условия:", _html_text(d.get("conditions"))),
    )


# --- International ATS ------------------------------------------------------


def _ats_slug(board: Board) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", board.slug):
        raise ValueError(f"некорректный slug: {board.slug!r}")
    return board.slug


def greenhouse_posting(board: Board, raw: dict) -> tuple[Posting, str]:
    location = (raw.get("location") or {}).get("name") or ""
    title = (raw.get("title") or "").strip()
    # `content` arrives HTML-escaped twice: entities around the markup.
    content = HTMLParser(f"<div>{raw.get('content') or ''}</div>").text()
    posting = Posting(
        id=_pid(board, raw.get("id")),
        url=raw.get("absolute_url") or "",
        title=title,
        company=board.name,
        location=location,
        remote=True if REMOTE_RE.search(location) else None,
        published=(raw.get("first_published") or "")[:10],
    )
    return posting, _join(("", _head(title, board.name, location)), ("", _html_text(content)))


def fetch_greenhouse(board: Board, queries: list[str], client: httpx.Client, out: Collected) -> None:
    r = client.get(
        f"https://boards-api.greenhouse.io/v1/boards/{_ats_slug(board)}/jobs",
        params={"content": "true"},
    )
    r.raise_for_status()
    for raw in r.json().get("jobs") or []:
        posting, text = greenhouse_posting(board, raw)
        out.postings.append(posting)
        out.texts[posting.id] = text


LEVER_WORKPLACE = {"remote": True, "onsite": False, "hybrid": False}


def lever_posting(board: Board, raw: dict) -> tuple[Posting, str]:
    categories = raw.get("categories") or {}
    location = categories.get("location") or ""
    title = (raw.get("text") or "").strip()
    lists = [
        (f"{item.get('text', '')}:", _html_text(item.get("content")))
        for item in raw.get("lists") or []
    ]
    created = raw.get("createdAt")
    published = ""
    if isinstance(created, int | float):
        from datetime import UTC, datetime

        published = datetime.fromtimestamp(created / 1000, UTC).date().isoformat()
    posting = Posting(
        id=_pid(board, raw.get("id")),
        url=raw.get("hostedUrl") or "",
        title=title,
        company=board.name,
        location=location,
        remote=LEVER_WORKPLACE.get(raw.get("workplaceType") or ""),
        published=published,
    )
    text = _join(
        ("", _head(title, board.name, location)),
        ("", raw.get("descriptionPlain")),
        *lists,
        ("", raw.get("additionalPlain")),
    )
    return posting, text


def fetch_lever(board: Board, queries: list[str], client: httpx.Client, out: Collected) -> None:
    r = client.get(f"https://api.lever.co/v0/postings/{_ats_slug(board)}", params={"mode": "json"})
    r.raise_for_status()
    for raw in r.json() or []:
        posting, text = lever_posting(board, raw)
        out.postings.append(posting)
        out.texts[posting.id] = text


def _ashby_remote(raw: dict) -> bool | None:
    if raw.get("isRemote") is True:
        return True
    workplace = (raw.get("workplaceType") or "").lower()
    if workplace == "remote":
        return True
    locations = [raw.get("location") or ""] + [
        s.get("location") or "" for s in raw.get("secondaryLocations") or []
    ]
    if any(REMOTE_RE.search(loc) for loc in locations):
        return True
    if workplace in ("onsite", "hybrid"):
        return False
    return None


def ashby_posting(board: Board, raw: dict) -> tuple[Posting, str]:
    location = raw.get("location") or ""
    title = (raw.get("title") or "").strip()
    posting = Posting(
        id=_pid(board, raw.get("id")),
        url=raw.get("jobUrl") or "",
        title=title,
        company=board.name,
        location=location,
        remote=_ashby_remote(raw),
        published=(raw.get("publishedAt") or "")[:10],
    )
    text = _join(("", _head(title, board.name, location)), ("", raw.get("descriptionPlain")))
    return posting, text


def fetch_ashby(board: Board, queries: list[str], client: httpx.Client, out: Collected) -> None:
    r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{_ats_slug(board)}")
    r.raise_for_status()
    for raw in r.json().get("jobs") or []:
        if raw.get("isListed") is False:
            continue
        posting, text = ashby_posting(board, raw)
        out.postings.append(posting)
        out.texts[posting.id] = text


FETCHERS: dict[str, Callable[[Board, list[str], httpx.Client, Collected], None]] = {
    "sber": fetch_sber,
    "yandex": fetch_yandex,
    "vk": fetch_vk,
    "huntflow": fetch_huntflow,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


def collect(
    queries: list[str],
    groups: tuple[str, ...] = ("ru",),
    boards: list[Board] | None = None,
    client: httpx.Client | None = None,
) -> Collected:
    """Read every board in the groups. One failing board is a problem, not a crash."""
    boards = [b for b in (boards if boards is not None else load()) if b.group in groups]
    out = Collected()
    own = client is None
    client = client or _client()
    seen: set[str] = set()
    try:
        for i, board in enumerate(boards):
            if i:
                time.sleep(DELAY_SECONDS)
            fetcher = FETCHERS.get(board.kind)
            if fetcher is None:
                out.problems.append({"board": board.name, "why": f"неизвестный тип {board.kind}"})
                continue
            before = len(out.postings)
            try:
                fetcher(board, queries, client, out)
            except (httpx.HTTPError, ValueError) as e:
                out.problems.append({"board": board.name, "why": f"{type(e).__name__}: {e}"[:200]})
            # Several queries hit the same vacancy; keep the first sighting.
            fresh = []
            for posting in out.postings[before:]:
                if posting.id not in seen:
                    seen.add(posting.id)
                    posting.accredited = board.accredited
                    fresh.append(posting)
            out.postings[before:] = fresh
    finally:
        if own:
            client.close()
    return out


def needs_description(posting_id: str) -> bool:
    return posting_id.split("/", 1)[0] in ("yandex", "vk", "huntflow")


def describe(
    postings: list[Posting],
    boards: list[Board] | None = None,
    client: httpx.Client | None = None,
    limit: int = MAX_DESCRIBED,
) -> dict[str, str]:
    """Descriptions for boards that do not list them. Run over survivors only.

    Never raises: a vacancy whose page did not load keeps no text and is
    fetched again when evaluated, rather than sinking the scan.
    """
    by_slug = {(b.kind, b.slug or b.kind): b for b in (boards if boards is not None else load())}
    todo = [p for p in postings if needs_description(p.id)][:limit]
    out: dict[str, str] = {}
    own = client is None
    client = client or _client()
    try:
        for i, posting in enumerate(todo):
            if i:
                time.sleep(DELAY_SECONDS)
            kind, slug, native = posting.id.split("/", 2)
            try:
                if kind == "yandex":
                    text = describe_yandex(native, client)
                elif kind == "vk":
                    text = describe_vk(native, client)
                else:
                    board = by_slug.get((kind, slug))
                    if board is None:
                        continue
                    text = describe_huntflow(board, native, client)
            except (httpx.HTTPError, ValueError):
                continue
            if text:
                out[posting.id] = text
    finally:
        if own:
            client.close()
    return out
