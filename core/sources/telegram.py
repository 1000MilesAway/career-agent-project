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
divisions, qualification and salary as fields, and core.filters works on those.
A Telegram post is flat text, so this module has to reconstruct what it can:
`Post.title` is the first non-hashtag line, and `looks_like_vacancy()` throws out
what is not an employer's job ad at all. Both are guesses, and both are cheap —
no model is called here.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

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
# reading: these map onto what core.filters wants and nothing else in Telegram does.
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
    # "channel/101", not "101". Telegram numbers posts within a channel, so
    # the bare number collides across channels — and the product stores it as
    # `external_id`, unique per (user, source, external_id). Two channels with
    # a post 101 collapsed into one row that kept the first post's text under
    # the second one's title and link, so an evaluation judged the wrong ad.
    post_id = post_path

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
    )


def looks_like_vacancy(post: Post) -> tuple[bool, str]:
    """Is this an employer's job ad? Free check, runs before any filter or model.

    Habr needs nothing like this — everything in its listing is a vacancy. A
    Telegram channel also carries memes, greetings, course ads, weekly stats and
    candidates' own CVs, and every one of those would otherwise be scored as a job.

    Salary is deliberately NOT required. Telegram states pay far less often than
    Habr, and making it mandatory dropped 6 of 9 genuine vacancies in the survey.
    """
    if len(post.text) < MIN_VACANCY_CHARS:
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
