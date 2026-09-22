"""Telegram job channels via the public `t.me/s/<channel>` web preview.

Why this route and not the Bot API or a userbot:
  - Bot API cannot read a channel the bot does not administer, and has no method
    for pulling history at all.
  - A userbot (Telethon) works, but it logs in as the user's own account and puts
    it at risk of a ban — too much to pay for data that is already public.
  - `t.me/s/<channel>` serves the last ~20 posts as plain HTML to anyone, with no
    key, no login and no rate limit we could trigger. Verified 2026-09-17.

GROUPS HAVE NO PREVIEW. `t.me/s/<name>` answers 302 for a chat (as opposed to a
channel), and that is not a block but a missing feature — there is no HTML to read.
`t.me/<name>` says which it is: "N members" for a chat, "N subscribers" for a
channel. Several of the liveliest job venues are chats and simply cannot be read
this way; data/sources/telegram-channels.json lists them under chats_unreachable.

The hard part is not fetching but STRUCTURE. A Habr posting arrives with title,
divisions, qualification and salary as fields, and probe.filters works on those.
A Telegram post is flat text, so this module has to reconstruct what it can:
`Post.title` is the first non-hashtag line, and `looks_like_vacancy()` throws out
what is not an employer's job ad at all. Both are guesses, and both are cheap —
no model is called here.

MANY POSTS ARE ONLY A TEASER. A growing share of channels post four lines —
role, format, company, pay — and put the actual description behind a link
("Смотреть вакансию" → telegra.ph). `body.text()` drops the href, so the
vacancy reaching the model was a 250-character stub while the requirements sat
one hop away. `fetch_externals()` follows that link; see DESCRIPTION_HOSTS for
why it is a whitelist and not "follow whatever is in the post".
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

BASE = "https://t.me"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Be a polite guest, as with Habr: one request at a time.
DELAY_SECONDS = 1.0

# A channel that has not posted in this long is treated as dead. `t.me/s/` always
# serves the LAST page, so a dead channel answers 200 with a full 20 posts and
# looks perfectly healthy — golangjob happily hands back February 2023. Without
# this check a scan quietly ships years-old vacancies.
MAX_SILENCE_DAYS = 30

# Below this a post is an aside, a poll or a greeting, never a job ad. The
# shortest real vacancy seen in the 2026-09-17 survey was ~250 characters.
MIN_VACANCY_CHARS = 200

# A teaser post carries almost no text of its own, so MIN_VACANCY_CHARS would
# throw it away — the description it points at never gets a chance to be read.
# A post this short still counts as a vacancy when it links to a description host.
MIN_TEASER_CHARS = 60

# Hosts that serve a job description as a plain public page. The list is a
# WHITELIST, not a blocklist, for three reasons:
#   - a job post links to many things (the HR's @handle, the company site, a
#     course ad, a referral tracker), and following all of them turns one scan
#     into dozens of requests to strangers' hosts;
#   - an arbitrary href is attacker-controlled input — a channel can post a link
#     to anything at all, and our fetcher must not be pointed at it;
#   - only these hosts actually repay the request with a full description.
# Widen it when a channel proves it uses something else; keep it data-shaped.
DESCRIPTION_HOSTS = (
    "telegra.ph",
    "graph.org",
    "teletype.in",
    "notion.site",
    "hh.ru",
    "career.habr.com",
)

# The fetched page replaces nothing — it is appended to the post. Cap it so one
# long article cannot blow up the triage batch that has to hold many vacancies.
MAX_EXTERNAL_CHARS = 8000

# The post is a candidate advertising themselves, not an employer hiring. Channels
# like django_jobs_board are half these, and they must not reach the report.
SEEKER_RE = re.compile(
    r"(#cv\b|#резюме|#opentowork|ищу работу|ищу проект|ищу заказы|ищу подработ"
    r"|open to work|рассматриваю предложения)",
    re.IGNORECASE,
)
# An employer is hiring. "Грейд:"/"Стек:" are in here because the runello_rus_*
# family uses that template and often skips the verb entirely.
HIRING_RE = re.compile(
    r"(#вакансия|#vacancy|ваканси|ищем|мы ищем|требуется|в команду|открыта позиция"
    r"|vacancy|ищет|hiring|we are looking|грейд:|стек:)",
    re.IGNORECASE,
)
# Some way to reach the employer. A job ad without one is a digest entry or an
# excerpt, not something the user could act on.
CONTACT_RE = re.compile(
    r"(@\w{4,}|t\.me/|https?://|откликн|сопроводительн|hh\.ru|резюме на|пишите|apply)",
    re.IGNORECASE,
)
# Course ads, webinars and channel cross-promotion dressed up as content. This is
# the user's main complaint about job channels, so it gets its own check.
PROMO_RE = re.compile(
    r"(курс[аыу]?\b|обучени|вебинар|интенсив|запишись|регистрируйся|промокод|скидк"
    r"|\berid\b|реклама|начни карьеру|с нуля до|научим|освой"
    r"|подборка каналов|папк[уа] telegram)",
    re.IGNORECASE,
)

# Fields the runello_rus_* family states explicitly. Free structure, so it is worth
# reading: these map onto what probe.filters wants and nothing else in Telegram does.
# A line that is only emoji and punctuation is decoration, not a title.
_HAS_WORD_RE = re.compile(r"[^\W_]", re.UNICODE)

GRADE_RE = re.compile(r"^\s*(?:грейд|grade|уровень)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
STACK_RE = re.compile(r"^\s*(?:стек|stack|технологии)\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
REMOTE_RE = re.compile(r"(удал[её]нк|удал[её]нн|remote|из любой точки|гибрид)", re.IGNORECASE)

# A salary, written the many ways a human writes one. Used as a signal, never as a
# filter: Telegram states pay far less often than Habr, and demanding it threw out
# two thirds of the real vacancies in the survey.
SALARY_RE = re.compile(
    r"(\d[\d  ]{2,}\s*(?:₽|руб|р\.|\$|€|USD|EUR)"
    r"|от\s*\d{2,}\s*(?:000|k|к|тыс)"
    r"|(?:вилка|зп|зарплат\w*|оклад|salary)\s*[:\-–—]?\s*\d)",
    re.IGNORECASE,
)


@dataclass
class Post:
    """One channel post, shaped as close to habr_api.Posting as the text allows."""

    id: str
    url: str
    channel: str
    text: str
    published: str = ""
    title: str = ""
    grade: str = ""
    stack: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    remote: bool = False
    has_salary: bool = False
    # Links found in the post body, in order, already filtered to DESCRIPTION_HOSTS.
    links: list[str] = field(default_factory=list)
    # Filled by fetch_externals(); empty means "not fetched" or "fetch failed".
    external_url: str = ""
    external_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "channel": self.channel,
            "title": self.title,
            "grade": self.grade,
            "stack": self.stack,
            "tags": self.tags,
            "remote": self.remote,
            "has_salary": self.has_salary,
            "published": self.published,
            "external_url": self.external_url,
        }

    def as_text(self) -> str:
        """The plain text the LLM pipeline expects, mirroring habr.Vacancy.as_text."""
        parts = [self.title or self.channel]
        parts.append(f"Источник: Telegram-канал @{self.channel}")
        if self.grade:
            parts.append(f"Грейд: {self.grade}")
        if self.stack:
            parts.append("Стек: " + ", ".join(self.stack))
        parts.append("")
        parts.append(self.text)
        if self.external_text:
            # Labelled, not merged: the model must be able to tell the channel's
            # own four lines from the employer's description, and a later reader
            # needs to know where the bulk of this text came from.
            parts.append("")
            parts.append(f"--- Полное описание со страницы {self.external_url} ---")
            parts.append(self.external_text)
        return "\n".join(parts)


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ru"},
        timeout=30.0,
        follow_redirects=False,  # a 302 means "this is a chat"; see fetch_channel.
    )


class NoPreview(Exception):
    """The name is a group, or does not exist: `t.me/s/` serves no HTML for it."""


def _first_title_line(text: str) -> str:
    """The first line carrying actual words — the closest thing to a title.

    Channels put the role on line one and the tags either above it or below, so
    skipping tag-only lines recovers the headline surprisingly reliably. A line
    with tags AND words (`#golang Senior Go Engineer`) is kept, minus the tags.

    A line must contain a letter or a digit to count. godevjob opens posts with a
    lone emoji ("🔥\nGo Developer\nот 150 000 ₽"), and taking that line literally
    gave every vacancy from that channel the title "🔥".
    """
    for raw in text.split("\n"):
        line = re.sub(r"#[\wЀ-ӿ]+", "", raw).strip(" -–—:|,\t")
        if _HAS_WORD_RE.search(line):
            return line
    return ""


def is_description_link(url: str) -> bool:
    """Does this href point at a host that serves a job description?

    Matches the host exactly or as a subdomain, never as a substring:
    "telegra.ph.evil.com" must not pass for containing "telegra.ph".
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    host = host.removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in DESCRIPTION_HOSTS)


def _description_links(body) -> list[str]:
    """Hrefs in the post body that point at a description host, deduped, in order.

    t.me links are skipped even though a post's own channel is on no whitelist:
    they are the HR's handle or a cross-post, never the description.
    """
    out: list[str] = []
    for a in body.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href.lower().startswith(("http://", "https://")):
            continue
        if is_description_link(href) and href not in out:
            out.append(href)
    return out


def _parse_post(node, channel: str) -> Post | None:
    """Build a Post from one `.tgme_widget_message_wrap` node."""
    body = node.css_first(".tgme_widget_message_text")
    if body is None:
        return None
    # `.text(separator=...)` keeps <br> line breaks, which carry the post's layout:
    # without them "Грейд: Senior" and the next field would run together.
    text = body.text(separator="\n", strip=True)
    if not text:
        return None

    holder = node.css_first(".tgme_widget_message")
    post_path = (holder.attributes.get("data-post") if holder else "") or ""
    post_id = post_path.split("/")[-1] if post_path else ""

    when = ""
    stamp = node.css_first("time[datetime]")
    if stamp is not None:
        when = stamp.attributes.get("datetime") or ""

    grade = GRADE_RE.search(text)
    stack = STACK_RE.search(text)
    return Post(
        id=post_id,
        url=f"{BASE}/{post_path}" if post_path else f"{BASE}/{channel}",
        channel=channel,
        text=text,
        published=when,
        title=_first_title_line(text),
        grade=grade.group(1).strip() if grade else "",
        stack=[s.strip() for s in stack.group(1).split(",") if s.strip()] if stack else [],
        tags=re.findall(r"#([\wЀ-ӿ]+)", text),
        remote=bool(REMOTE_RE.search(text)),
        has_salary=bool(SALARY_RE.search(text)),
        links=_description_links(body),
    )


def looks_like_vacancy(post: Post) -> tuple[bool, str]:
    """Is this an employer's job ad? Free check, runs before any filter or model.

    Habr needs nothing like this — everything in its listing is a vacancy. A
    Telegram channel also carries memes, greetings, course ads, weekly stats and
    candidates' own CVs, and every one of those would otherwise be scored as a job.

    Salary is deliberately NOT required. Telegram states pay far less often than
    Habr, and making it mandatory dropped 6 of 9 genuine vacancies in the survey.
    """
    # A teaser is short BECAUSE the description lives behind its link, so judging
    # it by length would drop exactly the posts this module now knows how to read.
    # It still has to clear every other check below.
    floor = MIN_TEASER_CHARS if post.links else MIN_VACANCY_CHARS
    if len(post.text) < floor:
        return False, "слишком короткий пост"
    # Checked before hiring words: "#резюме ... ищу работу" contains both, and the
    # seeker's own wording must win.
    if SEEKER_RE.search(post.text):
        return False, "резюме соискателя, не вакансия"
    # Promo is judged before the hiring check so the reason stays truthful: a course
    # ad usually has no hiring words either, and "нет признаков найма" would hide
    # what the post actually is. Promo only loses when the OPENING looks like
    # hiring — a real vacancy may well offer "обучение за счёт компании" further down.
    if PROMO_RE.search(post.text) and not HIRING_RE.search(post.text[:200]):
        return False, "реклама курсов или каналов"
    if not HIRING_RE.search(post.text):
        return False, "нет признаков найма"
    if not CONTACT_RE.search(post.text):
        return False, "нет контакта для отклика"
    return True, ""


def _readable_text(html: str) -> str:
    """The body text of a description page, scripts and chrome stripped.

    Deliberately generic: the whitelist spans telegra.ph, Teletype, Notion and
    hh.ru, and writing a per-host extractor for each would rot the moment any of
    them redesigns. Taking <article> when it exists and <body> otherwise gets the
    description everywhere, at the cost of some navigation text we can live with.
    """
    tree = HTMLParser(html)
    for bad in tree.css("script, style, nav, header, footer, noscript"):
        bad.decompose()
    node = tree.css_first("article") or tree.css_first("main") or tree.css_first("body")
    if node is None:
        return ""
    text = node.text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fetch_external(url: str, client: httpx.Client | None = None) -> str:
    """Read one linked description page. Free in money, ~1 s in time.

    Raises nothing: a description we could not read must not sink the scan, so
    every failure comes back as "" and the post keeps its own text.
    """
    if not is_description_link(url):
        return ""

    own = client is None
    client = client or httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.9"},
        timeout=20.0,
        follow_redirects=True,
    )
    try:
        r = client.get(url)
        if r.status_code != 200:
            return ""
        # A PDF or an image would come back as mojibake and be scored as text.
        if "html" not in r.headers.get("content-type", "").lower():
            return ""
        text = _readable_text(r.text)
    except httpx.HTTPError:
        return ""
    except Exception:
        # selectolax on a malformed page; one bad page is not a failed scan.
        return ""
    finally:
        if own:
            client.close()

    return text[:MAX_EXTERNAL_CHARS]


def fetch_externals(
    posts: list[Post],
    limit: int | None = None,
    client: httpx.Client | None = None,
) -> int:
    """Fill in `external_text` for posts that link to a description host.

    Costs no model money, but it is one HTTP request per post to someone else's
    host, so it runs AFTER the free filters have cut the list down — never over
    everything collected. Returns how many posts gained a description.

    `limit` caps the requests per scan. Without it a wide channel list could fire
    a hundred requests at telegra.ph in a row and deserve the block it would get.
    """
    targets = [p for p in posts if p.links and not p.external_text]
    if limit is not None:
        targets = targets[:limit]
    if not targets:
        return 0

    own = client is None
    client = client or httpx.Client(
        headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.9"},
        timeout=20.0,
        follow_redirects=True,
    )
    filled = 0
    try:
        for i, post in enumerate(targets):
            # Only the first whitelisted link: it is the "смотреть вакансию" one.
            # Later links are the company blog or the HR's other channel.
            url = post.links[0]
            text = fetch_external(url, client=client)
            if text:
                post.external_url = url
                post.external_text = text
                filled += 1
            if i < len(targets) - 1:
                time.sleep(DELAY_SECONDS)
    finally:
        if own:
            client.close()
    return filled


def _channel_is_silent(posts: list[Post], max_days: int) -> bool:
    stamps = [p.published for p in posts if p.published]
    if not stamps:
        return False
    try:
        newest = max(datetime.fromisoformat(s) for s in stamps)
    except ValueError:
        return False
    return datetime.now(UTC) - newest > timedelta(days=max_days)


def fetch_channel(
    channel: str,
    before: str | int | None = None,
    client: httpx.Client | None = None,
) -> list[Post]:
    """One page (~20 posts) of a public channel, newest last.

    Raises NoPreview when the name is a group or does not exist.
    """
    url = f"{BASE}/s/{channel}"
    if before:
        url = f"{url}?before={before}"

    own = client is None
    client = client or _client()
    try:
        r = client.get(url)
        # 302 -> t.me/<channel>: a group (or a missing name). No preview exists.
        if r.status_code in (301, 302, 303, 307, 308):
            raise NoPreview(f"@{channel}: нет веб-превью (группа или канала не существует)")
        r.raise_for_status()
        html = r.text
    finally:
        if own:
            client.close()

    tree = HTMLParser(html)
    posts = []
    for node in tree.css(".tgme_widget_message_wrap"):
        post = _parse_post(node, channel)
        if post is not None:
            posts.append(post)
    return posts


def search_many(
    channels: list[str],
    pages: int = 1,
    max_silence_days: int = MAX_SILENCE_DAYS,
    skip_non_vacancies: bool = True,
) -> tuple[list[Post], list[tuple[str, str]]]:
    """Collect from several channels. Free — no model is called.

    Returns (posts, problems), where problems pairs a channel with why it was
    skipped. Nothing is silently dropped: a dead or unreachable channel is a fact
    the user needs, not noise to swallow.

    Deduplication is BY TEXT, not by post id. Channels repost the same vacancy
    under new ids — getitrussia served 20 posts holding 9 distinct texts — and
    id-based dedup would let every copy through to be scored separately.
    """
    seen: set[str] = set()
    merged: list[Post] = []
    problems: list[tuple[str, str]] = []

    with _client() as client:
        for i, channel in enumerate(channels):
            try:
                collected: list[Post] = []
                cursor: str | int | None = None
                for page in range(pages):
                    batch = fetch_channel(channel, before=cursor, client=client)
                    if not batch:
                        break
                    collected.extend(batch)
                    # `before` pages backwards from the oldest id on this page.
                    cursor = min((p.id for p in batch if p.id.isdigit()), key=int, default=None)
                    if cursor is None:
                        break
                    if page < pages - 1:
                        time.sleep(DELAY_SECONDS)
            except NoPreview as e:
                problems.append((channel, str(e)))
                continue
            except httpx.HTTPError as e:
                problems.append((channel, f"@{channel}: {type(e).__name__}"))
                continue

            if not collected:
                problems.append((channel, f"@{channel}: постов не найдено"))
                continue
            if _channel_is_silent(collected, max_silence_days):
                newest = max(p.published for p in collected if p.published)[:10]
                problems.append((channel, f"@{channel}: молчит с {newest} — пропускаю"))
                continue

            for post in collected:
                if skip_non_vacancies and not looks_like_vacancy(post)[0]:
                    continue
                key = " ".join(post.text.split())
                if key in seen:
                    continue
                seen.add(key)
                merged.append(post)

            if i < len(channels) - 1:
                time.sleep(DELAY_SECONDS)

    return merged, problems
