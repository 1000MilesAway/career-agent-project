"""Telegram chats through tg-provider, a userbot (Telethon) on another host.

Why a provider (docs/decisions.md, 2026-10-15 and 2026-10-16): chats have no
`t.me/s/` preview, so reading them takes a logged-in account. That account
and its session live on the sources server (185.153.180.173) in their own
repository; this app never imports Telethon. The provider scans its chats on
a schedule (once a day) and serves what it collected as JSON over HTTP, the
same arrangement as hh-scrapper.

A separate source from `telegram` (the channel previews), named `telethon`:
it reads different places through a different door, fails differently (a
banned or logged-out account, a scan that stopped running), and the user
sees which of the two a vacancy came from.

A chat message is parsed exactly like a channel post (`telegram.build_post`)
and goes through the same `looks_like_vacancy` check: a job chat carries
candidates' CVs, questions and chatter next to the vacancies.

Nothing here touches the database; configuration comes from the environment.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from . import telegram

SOURCE = "telethon"
# How far back one read reaches. The match window (services/scan.py) is two
# weeks; a message older than that would be pooled only to age out.
DAYS = 14
# The provider scans once a day. A day and a half without a finished scan
# means the cron, the session or the account broke, and the user is told so
# instead of being served a frozen list as if it were today's.
STALE_AFTER = timedelta(hours=36)

# Links are plain text in a chat message as the provider hands it over (no
# markup), so the apply and description links are found in the text itself.
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


class NotConfigured(RuntimeError):
    """TG_PROVIDER_URL or TG_PROVIDER_TOKEN is not set."""


class ProviderError(RuntimeError):
    """The provider did not answer, or answered with an error."""


def _config() -> tuple[str, str]:
    url = os.getenv("TG_PROVIDER_URL", "").strip().rstrip("/")
    token = os.getenv("TG_PROVIDER_TOKEN", "").strip()
    if not url or not token:
        raise NotConfigured(
            "источник Telegram-чатов не настроен: нет TG_PROVIDER_URL или TG_PROVIDER_TOKEN"
        )
    return url, token


def configured() -> bool:
    try:
        _config()
    except NotConfigured:
        return False
    return True


def _call(method: str, path: str, params: Any = None, json: Any = None) -> dict[str, Any]:
    base, token = _config()
    try:
        r = httpx.request(
            method,
            f"{base}{path}",
            params=params,
            json=json,
            headers={"Authorization": f"Bearer {token}"},
            # The provider reads or writes a file it already has; no Telegram
            # request happens on this call.
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
    except httpx.HTTPError as e:
        raise ProviderError(f"провайдер Telegram-чатов не ответил: {type(e).__name__}") from e
    if r.status_code != 200:
        raise ProviderError(f"провайдер Telegram-чатов ответил {r.status_code}: {r.text[:120]}")
    return r.json()


def _get(path: str, params: Any = None) -> dict[str, Any]:
    return _call("GET", path, params=params)


def list_chats() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The provider's chat list (`chat`, `enabled`, `added_at`) and last scan status."""
    data = _get("/chats")
    return list(data.get("chats") or []), dict(data.get("status") or {})


def set_chat(name: str, enabled: bool) -> dict[str, Any]:
    """Add a chat to the provider's list, or switch one on or off.

    The provider owns the list because its cron scan reads it; the next
    scan (once a day) reads a newly added chat. Raises ProviderError with
    the provider's words for a name that is not a chat.
    """
    return _call("PUT", f"/chats/{quote(name.strip(), safe='')}", json={"enabled": enabled})


def as_post(record: dict[str, Any]) -> telegram.Post | None:
    """One provider record as a channel Post, or None when it cannot be one.

    The id is `chat/message_id`, like a preview post's `channel/101`: message
    numbers repeat across chats. A private group has no permalink; its
    messages still get a stable id, and the row links to nothing rather than
    to a page that does not exist.
    """
    chat, message_id, text = record.get("chat"), record.get("message_id"), record.get("text")
    if not chat or message_id is None or not text:
        return None
    path = f"{chat}/{message_id}"
    post = telegram.build_post(
        chat, path, text, record.get("date") or "", _URL_RE.findall(text)
    )
    post.url = record.get("link") or ""
    return post


def _problems(status: dict[str, Any], now: datetime) -> list[tuple[str, str]]:
    """What the provider's last scan reports as wrong, in the user's words."""
    out: list[tuple[str, str]] = []
    if status.get("error"):
        out.append(("tg-provider", f"последний скан чатов упал: {status['error']}"[:300]))
    finished = status.get("finished_at")
    try:
        when = datetime.fromisoformat(finished) if finished else None
    except ValueError:
        when = None
    if when is None:
        out.append(("tg-provider", "провайдер ещё ни разу не завершил скан чатов"))
    elif now - when > STALE_AFTER:
        out.append(("tg-provider", f"чаты не сканировались с {when:%Y-%m-%d %H:%M} UTC"))
    for chat in status.get("chats") or []:
        if chat.get("error"):
            out.append((chat.get("chat") or "?", f"@{chat.get('chat')}: {chat['error']}"[:300]))
    return out


def collect(days: int = DAYS) -> tuple[list[telegram.Post], list[tuple[str, str]], list[str]]:
    """Vacancies from the provider's chats: (posts, problems, chats read).

    Free — no model is called. A non-vacancy (CV, question, chatter) is not
    pooled, as with channel previews. Copies are merged by text: people post
    the same vacancy to a chat again a few days later.
    """
    data = _get("/posts", {"days": days})
    problems = _problems(data.get("status") or {}, datetime.now(UTC))
    seen: set[str] = set()
    posts: list[telegram.Post] = []
    for record in data.get("posts") or []:
        post = as_post(record)
        if post is None or not telegram.looks_like_vacancy(post)[0]:
            continue
        key = " ".join(post.text.split())
        if key in seen:
            continue
        seen.add(key)
        posts.append(post)
    return posts, problems, list(data.get("chats") or [])
