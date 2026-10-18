"""Finding Telegram job channels nobody has listed yet.

Three leads, all free (docs/decisions.md, 2026-10-18):

  - Telegram's own "similar channels" for the channels we read, by overlap
    of subscribers. The tg-provider userbot asks for them once a week
    (`channels.getChannelRecommendations` is for user accounts only);
  - channels posting `#вакансия`, from the same userbot's hashtag search —
    mostly spam farms, so they count only after the preview check below;
  - `t.me/<channel>` links in the posts already in the pool: job channels
    advertise their siblings.

A lead is never trusted as is. Each one's `t.me/s/` preview is read once and
judged here: enough recent posts, most of them vacancies (`looks_like_vacancy`,
the same check a scan applies), in Russian, with an audience. Even a channel
that passes is added hidden: the owner turns it on in the admin page.

Nothing here touches the database.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from . import telegram

# The preview shows the newest ~20 posts; fewer says too little to judge.
MIN_POSTS = 5
# Share of the preview's posts that pass `looks_like_vacancy`. Similar
# channels include the stack's news and interview-question channels, which
# post a vacancy now and then; a job channel is mostly vacancies.
MIN_VACANCY_SHARE = 0.5
# The product is for the Russian market; the hashtag search returns English
# bot farms by the hundred.
MIN_CYRILLIC_SHARE = 0.5
MIN_SUBSCRIBERS = 300
# Previews read per run, a polite request each (telegram.DELAY_SECONDS x2).
MAX_CHECKS = 60

_LINK_RE = re.compile(r"(?<![\w.])t\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{4,31})(?![A-Za-z0-9_])")
# Paths of t.me that are not channels.
_NOT_CHANNELS = frozenset({"joinchat", "addlist", "share", "proxy", "socks", "iv", "addstickers",
                           "setlanguage", "addtheme", "boost", "contact", "login"})
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)


def linked_channels(text: str) -> set[str]:
    """Channel names a post links to. Bots are left out: a bot is not a feed."""
    out = set()
    for name in _LINK_RE.findall(text or ""):
        if name.casefold() in _NOT_CHANNELS or name.casefold().endswith("bot"):
            continue
        out.add(name)
    return out


@dataclass
class Lead:
    """A channel worth a look, and every way it came up."""

    name: str
    via: list[str] = field(default_factory=list)

    def add(self, why: str) -> None:
        if why not in self.via:
            self.via.append(why)


@dataclass
class Verdict:
    name: str
    accepted: bool
    reason: str
    title: str = ""
    subscribers: int | None = None
    posts: int = 0
    vacancies: int = 0
    last_post_at: datetime | None = None


def judge(name: str, preview: telegram.Preview, now: datetime | None = None) -> Verdict:
    """Is this a live Russian job channel? Free: the preview is already read."""
    posts = preview.posts
    vacancies = sum(1 for p in posts if telegram.looks_like_vacancy(p)[0])
    stamps = []
    for p in posts:
        try:
            stamps.append(datetime.fromisoformat(p.published))
        except ValueError:
            continue
    last = max(stamps) if stamps else None
    # The preview's own spelling: a link may say "PyPiJob", the posts say "pypijob",
    # and the posts' ids are what the hide switch is matched against.
    canonical = posts[0].id.split("/", 1)[0] if posts and "/" in posts[0].id else name
    verdict = Verdict(
        name=canonical, accepted=False, reason="", title=preview.title,
        subscribers=preview.subscribers, posts=len(posts), vacancies=vacancies,
        last_post_at=last,
    )
    cyrillic = sum(1 for p in posts if _CYRILLIC_RE.search(p.text))
    now = now or datetime.now(UTC)
    if len(posts) < MIN_POSTS:
        verdict.reason = f"в превью {len(posts)} постов"
    elif last is None or (now - last).days > telegram.MAX_SILENCE_DAYS:
        verdict.reason = f"молчит с {last:%Y-%m-%d}" if last else "у постов нет дат"
    elif preview.subscribers is not None and preview.subscribers < MIN_SUBSCRIBERS:
        verdict.reason = f"подписчиков {preview.subscribers}"
    elif cyrillic < len(posts) * MIN_CYRILLIC_SHARE:
        verdict.reason = f"по-русски {cyrillic} постов из {len(posts)}"
    elif vacancies < len(posts) * MIN_VACANCY_SHARE:
        verdict.reason = f"вакансий {vacancies} из {len(posts)}"
    else:
        verdict.accepted = True
        verdict.reason = f"вакансий {vacancies} из {len(posts)}"
    return verdict


def check(leads: list[Lead]) -> list[Verdict]:
    """Read each lead's preview and judge it. Network; one request per lead."""
    out: list[Verdict] = []
    with telegram._client() as client:
        for i, lead in enumerate(leads):
            if i:
                time.sleep(telegram.DELAY_SECONDS * 2)
            try:
                preview = telegram.fetch_preview(lead.name, client=client)
            except telegram.NoPreview:
                out.append(Verdict(lead.name, False, "нет веб-превью: группа, человек или бот"))
                continue
            except (telegram.MarkupChanged, httpx.HTTPError) as e:
                # Not a verdict on the channel: it is tried again next run.
                out.append(Verdict(lead.name, False, f"не прочитан: {type(e).__name__}"))
                continue
            out.append(judge(lead.name, preview))
    return out
