"""The link a Telegram post gives the user to actually apply with.

A post is free text ending, usually, in a link: to hh.ru, to an ATS, to the
employer's own page. That link is the shortest verifiable path from "this
vacancy looks good" to "I have applied" — and since career-agent never applies
on the user's behalf, handing them the right link IS the product's last step.
Without this the row points at the post, and the user has to re-find the apply
path by hand.

Ported from career-ops, combining both of its providers: the host
classification and digest rule from the upstream `telegram-channel.mjs`, the
generic-vs-posting path ranking from `telegram.mjs`.

Three rules earned by live posts on configured channels:
  - a channel's promo block ("подпишись на наши каналы") is never an apply link;
  - a shortener's target cannot be verified without following the redirect,
    which a collector must not do, so it never becomes the link;
  - `ya_jobs` prefixes every post with a `/jobs/pages/benefits` banner. Taking
    the first link would give an entire channel ONE url — and the url is the
    dedup key, so the channel would collapse to a single vacancy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

# Telegram itself, social networks, and the places a post links for reasons
# other than applying (an article, a repo, a store page).
NON_APPLY_HOSTS = re.compile(
    r"^(?:t\.me|telegram\.me|telegram\.org|telegram\.dog|telegra\.ph|telega\.in"
    r"|vk\.com|vk\.cc|ok\.ru|max\.ru|facebook\.com|fb\.com|instagram\.com"
    r"|youtube\.com|youtu\.be|x\.com|twitter\.com|tiktok\.com|threads\.net"
    r"|store\.steampowered\.com|play\.google\.com|apps\.apple\.com"
    r"|substack\.com|medium\.com|habr\.com|github\.com)$",
    re.IGNORECASE,
)

# Target unverifiable without following the redirect. `clc.to` is the one seen
# live on godevjob/pydevjob, in the ad slot.
SHORTENER_HOSTS = re.compile(
    r"^(?:goo\.gl|bit\.ly|t\.co|cjl\.ist|clck\.ru|tglink\.io|tinyurl\.com"
    r"|cutt\.ly|is\.gd|ow\.ly|rb\.gy|surl\.li|u\.to)$",
    re.IGNORECASE,
)

# A form is where some channels collect responses. It is a real apply path, but
# it is the CHANNEL's funnel, not the employer's posting, and it carries no
# vacancy identity — two vacancies share one form, which would collapse them
# under the dedup key.
FORM_HOSTS = re.compile(
    r"^(?:forms\.gle|docs\.google\.com|forms\.yandex\.ru|typeform\.com"
    r"|tally\.so|airtable\.com|notion\.site|notion\.so)$",
    re.IGNORECASE,
)

# A board mirroring the vacancy. Still a fine apply link — just outranked by the
# employer's own page when the post carries both.
BOARD_HOSTS = re.compile(
    r"^(?:getmatch\.ru|finder\.work|geekjob\.ru|geeklink\.io|linkedin\.com"
    r"|hh\.ru|career\.habr\.com|djinni\.co|indeed\.com|glassdoor\.com"
    r"|weworkremotely\.com|remoteok\.com|arbeitnow\.com|cryptojobslist\.com"
    r"|relocate\.me|lemon\.io|rabota\.ru|superjob\.ru|zarplata\.ru)$",
    re.IGNORECASE,
)

# The path names a specific posting: a numeric id, a uuid, a long slug, or a
# hiring word. A bare year (2026) does not count — that is a blog post.
VACANCY_EVIDENCE_RE = re.compile(
    r"/(?:vacanc|vakans|job|position|opening|career|apply|hiring)"
    r"|[-_/](?!(?:19|20)\d{2}(?=[/?#]|$))\d{4,}(?=[/?#]|$)"
    r"|/j/[A-Z0-9]{6,}(?=[/?#]|$)"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# A company's GENERIC page, not a posting: the benefits banner, an about page, a
# recruiter's own profile. Scored below a real posting link rather than banned,
# so a post whose only link is one of these still gets a url.
GENERIC_PATH_RE = re.compile(
    r"/(?:pages|about|benefits|company|team|blog|news|help|faq|privacy|terms"
    r"|life|culture|employees|in|profile|resume)(?:/|$)",
    re.IGNORECASE,
)

TRACKING_PARAM_RE = re.compile(
    r"^(?:utm_|ref$|referrer$|source$|src$|s$|from$|campaign$|fbclid$|gclid$"
    r"|yclid$|mc_|_hs|igshid$|trk$)",
    re.IGNORECASE,
)

# The listing root itself ("all our vacancies"), not one vacancy.
LISTING_ROOT_RE = re.compile(
    r"^/(?:jobs|vacancies|careers|career|vacancy|vakansii|job)$", re.IGNORECASE
)
# A homepage, with or without a locale prefix.
LOCALE_ROOT_RE = re.compile(r"^(?:/[a-z]{2}(?:-[a-z]{2})?)?$", re.IGNORECASE)

EMPLOYER = "employer"
BOARD = "board"


@dataclass(frozen=True)
class ApplyLink:
    url: str
    kind: str  # EMPLOYER | BOARD


def _host(parts) -> str:
    return parts.hostname.lower().removeprefix("www.") if parts.hostname else ""


def classify(href: str) -> str | None:
    """EMPLOYER, BOARD, or None when the link is not an apply path at all."""
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    if parts.scheme != "https":
        return None
    host = _host(parts)
    if not host:
        return None
    if NON_APPLY_HOSTS.match(host) or SHORTENER_HOSTS.match(host) or FORM_HOSTS.match(host):
        return None

    path = parts.path.rstrip("/")
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return BOARD if "/jobs/view/" in path else None

    # A homepage or a listing root stays one even with a tracking tail
    # (`/?utm_source=telegram` is what channel footers append); only a parameter
    # that could identify a vacancy (`?gh_jid=123`) makes it a page.
    identifying = any(not TRACKING_PARAM_RE.match(k) for k, _ in parse_qsl(parts.query))
    if not identifying:
        if LOCALE_ROOT_RE.match(path) or LISTING_ROOT_RE.match(path):
            return None
        if not VACANCY_EVIDENCE_RE.search(path):
            return None
    return BOARD if BOARD_HOSTS.match(host) else EMPLOYER


def vacancy_key(href: str) -> str:
    """Identity of a vacancy link, ignoring tracking tails and the fragment.

    The same page linked twice with different `utm_` tails is one vacancy;
    `?gh_jid=1` and `?gh_jid=2` on one path are two.
    """
    parts = urlsplit(href)
    keep = sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAM_RE.match(k))
    return f"{_host(parts)}{parts.path.rstrip('/')}?{urlencode(keep)}"


def pick(hrefs: list[str]) -> ApplyLink | None:
    """The apply link for a post, or None when it carries none we trust.

    The employer's own page beats a board mirror of the same vacancy; a specific
    posting path beats a generic company page; within a class the first link in
    the post wins.

    A post carrying two or more DISTINCT vacancies of one kind is a digest, not
    a posting — there is no way to split it into the jobs it bundles, so it
    yields None and falls back to the permalink. One board page plus the
    employer's own page is the same vacancy mirrored, which is the normal shape
    and must not count as a digest.
    """
    seen: set[str] = set()
    qualified: list[tuple[ApplyLink, bool]] = []
    for href in hrefs:
        kind = classify(href)
        if not kind:
            continue
        key = vacancy_key(href)
        if key in seen:
            continue
        seen.add(key)
        generic = bool(GENERIC_PATH_RE.search(urlsplit(href).path))
        qualified.append((ApplyLink(href, kind), generic))

    if not qualified:
        return None

    # Digest check counts only SPECIFIC pages: a post may well carry the
    # benefits banner and one vacancy, and that is not a digest.
    specific = [link for link, generic in qualified if not generic]
    for kind in (EMPLOYER, BOARD):
        if len([link for link in specific if link.kind == kind]) > 1:
            return None

    for candidates in (specific, [link for link, _ in qualified]):
        for kind in (EMPLOYER, BOARD):
            for link in candidates:
                if link.kind == kind:
                    return link
    return None
