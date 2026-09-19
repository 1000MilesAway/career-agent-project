"""Collect from Telegram channels and pre-screen — the free half of the pipeline.

Shared by the CLI (`probe.run tg`) and the web screen. The logic lives here
rather than in either caller because the two must not drift: a filter that
runs in the CLI but not on screen would make the demo lie about what the
product does.

No model is called anywhere in this module. It is stages 1-2 of the pipeline
(collect, then filter for free) applied to a source that has no fields.
"""

from __future__ import annotations

from typing import Any

from .filters import PreScreen, SearchProfile
from .sources import habr_api, telegram, tg_channels


def as_posting(post: telegram.Post) -> habr_api.Posting:
    """Dress a Telegram post as a Habr posting so one PreScreen serves both.

    Divisions stay empty — Telegram states no specialization — so the
    specialization checks simply do not fire rather than guessing.
    """
    return habr_api.Posting(
        id=post.id,
        url=post.url,
        title=post.title,
        company=f"@{post.channel}",
        qualification=post.grade,
        remote=post.remote,
        skills=post.stack,
    )


def collect(
    channels: list[str] | None = None,
    groups: tuple[str, ...] = ("core",),
    pages: int = 1,
    max_silence_days: int = telegram.MAX_SILENCE_DAYS,
    keep_all: bool = False,
    search_profile: dict | None = None,
) -> dict[str, Any]:
    """Collect, then pre-screen when a search profile exists.

    Without a profile this is a collection run, which is still the useful half:
    nothing is dropped that the user never configured.
    """
    names = channels or tg_channels.channel_names(groups=groups)
    if not names:
        return {"channels": [], "collected": 0, "problems": [], "kept": [], "dropped": []}

    posts, problems = telegram.search_many(
        names, pages=pages, max_silence_days=max_silence_days,
        skip_non_vacancies=not keep_all,
    )

    if not search_profile:
        return {
            "channels": names,
            "collected": len(posts),
            "problems": [{"channel": c, "why": w} for c, w in problems],
            "kept": [(p, "") for p in posts],
            "dropped": [],
            "filtered": False,
        }

    screen = PreScreen(SearchProfile.from_dict(search_profile))
    kept: list[tuple[telegram.Post, str]] = []
    dropped: list[tuple[telegram.Post, str]] = []
    for p in posts:
        decision = screen.check(as_posting(p))
        (kept if decision.keep else dropped).append((p, decision.reason))

    return {
        "channels": names,
        "collected": len(posts),
        "problems": [{"channel": c, "why": w} for c, w in problems],
        "kept": kept,
        "dropped": dropped,
        "filtered": True,
    }


def drop_reasons(dropped: list[tuple[telegram.Post, str]]) -> dict[str, int]:
    """Why postings were thrown away, counted. The screen must stay auditable."""
    reasons: dict[str, int] = {}
    for _post, why in dropped:
        key = why.split(":")[0]
        reasons[key] = reasons.get(key, 0) + 1
    return reasons


def as_rows(kept: list[tuple[telegram.Post, str]]) -> list[dict]:
    """Survivors in the shape the found-store and the screen expect.

    A Telegram post carries no salary field and no specialization, so the row
    says so plainly rather than leaving the cells empty and looking broken.
    """
    rows = []
    for post, _why in kept:
        rows.append({
            "id": post.id,
            "url": post.url,
            "title": post.title,
            "company": f"@{post.channel}",
            "qualification": post.grade,
            "remote": post.remote,
            "salary_text": "зарплата указана" if post.has_salary else "",
            "skills": post.stack,
            "published": post.published,
            "source": "telegram",
            # The post text is the vacancy: there is no page to fetch later, so
            # an evaluation has to read what was collected here.
            "text": post.as_text(),
        })
    return rows
