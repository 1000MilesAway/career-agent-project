"""The link behind which a Telegram post keeps the actual job description.

A growing share of channels post a teaser — role, format, company, pay, and
"Смотреть вакансию" pointing at telegra.ph. `t.me/pythonrabota/2340` is 252
characters; its telegra.ph page is 2018, and everything an evaluation needs
(responsibilities, requirements, stack) is on the page, not in the post. Judging
the teaser means scoring a headline and a salary band.

WHY A WHITELIST AND NOT "FOLLOW WHATEVER IS IN THE POST". Three reasons, each
sufficient on its own:
  - a job post links to many things — the HR's @handle, the company's site, a
    referral tracker, a course ad — and following all of them turns one scan
    into dozens of requests to strangers' hosts;
  - an href in someone else's channel is input we do not control, and a fetcher
    must not be pointed at an arbitrary address on its say-so;
  - only these hosts repay the request with a description. The rest serve a
    landing page, and we would have paid the request to learn nothing.

This is the mirror image of `apply_link`: that module answers "where does the
user apply" and refuses telegra.ph, because nobody applies through an article.
This one answers "where is the vacancy described", and telegra.ph is the most
common answer. A post can carry both, and they are different links.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Hosts that serve a job description as a plain public page.
#
# Article platforms first — they are what channels actually use for teasers.
# The job boards are here because a post sometimes links straight to the full
# posting; `apply_link` also lists them, for the other question.
#
# Keep this list short and evidenced. A host earns its place by having been
# seen carrying a description, not by looking plausible.
DESCRIPTION_HOSTS = (
    "telegra.ph",
    "graph.org",
    "teletype.in",
    "notion.site",
    "hh.ru",
    "career.habr.com",
    "getmatch.ru",
    "geekjob.ru",
)


def is_description_link(href: str) -> bool:
    """Does this href point at a host that serves a job description?

    Matches the host exactly or as a subdomain, never as a substring:
    "telegra.ph.evil.com" must not pass for containing "telegra.ph". https only,
    for the same reason `apply_link.classify` requires it — a plaintext fetch of
    someone else's link is not something to do on a channel's say-so.
    """
    try:
        parts = urlsplit(href)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in DESCRIPTION_HOSTS)


def pick(hrefs: list[str]) -> str:
    """The first link worth fetching a description from, or "".

    First, not best: unlike an apply link there is nothing to rank here. The
    teaser's "Смотреть вакансию" is the opening link of the post, and later
    whitelisted links are the company blog or a neighbouring vacancy.
    """
    for href in hrefs:
        if is_description_link(href):
            return href
    return ""
