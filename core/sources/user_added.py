"""A vacancy the user brings to «Избранное» by its link.

For postings no scan found, or one the user saw elsewhere: "here is a
vacancy, is it worth it?". The link ends in the same `Posting` the free
filters read and the same plain text the evaluation reads, so nothing
downstream needs to know where the vacancy came from.

Only links to sources we already read are accepted. Habr, hh and geekjob are
read on demand by the source's own reader, and the vacancy lands in the shared
pool under that source and id — the same posting a scan would find. A link to
an employer's career site is not fetched at all: the app looks it up among the
postings a scan already pooled (`OtherSite`). Anything else — Telegram
included, where a post can say anything — is refused. Fetching a page the user
names would turn the server into their proxy (SSRF), and pasted text was
dropped with it: see docs/decisions.md, 2026-10-08.

Nothing here touches the database; the network is only in `from_url`, and
only to the hosts the scans already read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import geekjob, habr, hh, jobposting, salary_text
from .habr_api import Posting

# Postings from the pasted texts of before 2026-10-08. None are made any more;
# the constant stays so those rows are still deleted with the vacancy and the
# account.
TEXT_SOURCE = "text"

LINK_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)

NOT_ACCEPTED = "Принимаем ссылки только с проверенных ресурсов."


class OtherSite(ValueError):
    """A link to a site we do not read on demand. Never fetched: the caller may
    still find it among the postings a scan pooled, by `url_variants`."""


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
    """A single URL and nothing else."""
    return bool(LINK_RE.match(raw.strip()))


def from_url(url: str) -> Added:
    """A vacancy by its page on a board we scan. Network; ValueError says why not."""
    url = url.strip()
    if not is_link(url):
        raise ValueError(NOT_ACCEPTED)
    if habr.is_habr_url(url):
        return _from_habr(url)
    if hh.is_hh_url(url):
        return _from_hh(url)
    if geekjob.is_geekjob_url(url):
        page = geekjob.fetch_vacancy(url)
        m = geekjob.VACANCY_PATH_RE.match(urlsplit(url).path.rstrip("/"))
        if m is None:
            raise ValueError("Это не ссылка на вакансию geekjob.ru.")
        return _from_jobposting("geekjob", m.group(1), url.split("?")[0], page)
    if telegram_post(url):
        raise ValueError(NOT_ACCEPTED)
    raise OtherSite(NOT_ACCEPTED)


def url_variants(url: str) -> list[str]:
    """The spellings one page's link is stored under: without the fragment,
    with and without the trailing slash — a scan stores the board's own form."""
    base = url.strip().split("#", 1)[0]
    stem = base.rstrip("/")
    return list(dict.fromkeys([base, stem, stem + "/"]))


def telegram_post(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in ("t.me", "telegram.me")


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
        raise ValueError("Ссылки на hh.ru сейчас не читаются.") from e
    except hh.ProviderError as e:
        raise ValueError(f"hh.ru не ответил: {e}.") from e
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
