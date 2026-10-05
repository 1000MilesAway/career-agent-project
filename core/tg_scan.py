"""Collect from Telegram channels and pre-screen — the free half of the pipeline.

Collection and free filtering for Telegram. The logic lives here
rather than in either caller because the two must not drift: a filter that
runs in the CLI but not on screen would make the demo lie about what the
product does.

No model is called anywhere in this module. It is stages 1-2 of the pipeline
(collect, then filter for free) applied to a source that has no fields.
"""

from __future__ import annotations

from typing import Any

from .defaults import starter_profile
from .filters import PreScreen, SearchProfile
from .sources import habr_api, telegram, tg_channels


def as_posting(post: telegram.Post) -> habr_api.Posting:
    """Dress a Telegram post as a Habr posting so one PreScreen serves both.

    Divisions stay empty — Telegram states no specialization — so the
    specialization checks simply do not fire rather than guessing.

    The salary band is carried over ONLY when it was parsed in the currency the
    profile's threshold is written in. `salary_min` is a bare number with no
    currency (core/filters.py), so handing it 4500 for a "$4500/мес" posting
    would read as 4500 ₽ and drop a well-paid job. A foreign-currency band
    therefore travels as text and the check does not fire — the same "don't
    penalize missing data" default the rest of PreScreen uses.
    """
    rub = post.salary_currency == "RUB"
    return habr_api.Posting(
        id=post.id,
        url=post.url,
        title=post.title,
        company=f"@{post.channel}",
        qualification=post.grade,
        remote=post.remote,
        skills=post.stack,
        salary_from=post.salary_from if rub else None,
        salary_to=post.salary_to if rub else None,
        salary_text=salary_label(post),
        currency=post.salary_currency,
        # The post's date is the only age a Telegram vacancy has: a live post
        # says nothing about the job (services/liveness.py `stale_reason`).
        published=post.published,
    )


def salary_label(post: telegram.Post) -> str:
    """What the post says about pay, for the row and for the rejection reason.

    A drop reason reading "зарплата ниже порога: " with nothing after it tells
    the user nothing, and the invariant is that every rejection explains itself.
    """
    if post.salary_from is None and post.salary_to is None:
        return "зарплата указана" if post.has_salary else ""
    sign = {"RUB": "₽", "USD": "$", "EUR": "€"}.get(post.salary_currency, post.salary_currency)
    if post.salary_from and post.salary_to and post.salary_from != post.salary_to:
        return f"{post.salary_from:,} — {post.salary_to:,} {sign}".replace(",", " ")
    if not post.salary_from and post.salary_to:
        return f"до {post.salary_to:,} {sign}".replace(",", " ")
    return f"от {post.salary_from:,} {sign}".replace(",", " ")


# How many linked descriptions one scan may read. Free in money — this is HTTP,
# not the model — but each is a request to someone else's host, so it is capped
# rather than left to grow with the channel list.
MAX_ENRICHED = 30


def collect(
    channels: list[str] | None = None,
    groups: tuple[str, ...] = ("core",),
    pages: int = 1,
    max_silence_days: int = telegram.MAX_SILENCE_DAYS,
    keep_all: bool = False,
    search_profile: dict | None = None,
    enrich: bool = True,
) -> dict[str, Any]:
    """Collect, pre-screen, then read the descriptions the survivors link to.

    Without a profile this is a collection run, which is still the useful half:
    nothing is dropped that the user never configured.

    ORDER MATTERS. The descriptions are fetched LAST, over the survivors only.
    Money is not the constraint here, someone else's bandwidth is — and the
    pipeline's rule that the costly stage runs on the short list covers that
    just as much as it covers paid calls.
    """
    names = channels or tg_channels.channel_names(groups=groups)
    if not names:
        return {"channels": [], "collected": 0, "problems": [], "kept": [], "dropped": []}

    posts, problems = telegram.search_many(
        names, pages=pages, max_silence_days=max_silence_days,
        skip_non_vacancies=not keep_all,
    )

    # Returning everything unscreened was the Habr bug by another route:
    # Telegram survivors land in the same list, so an unfiltered pass fills it
    # with posts nobody asked for and no reason attached. Callers derive the
    # profile before getting here; the template is the last line of defence.
    profile = (
        SearchProfile.from_dict(search_profile) if search_profile else starter_profile()
    )
    screen = PreScreen(profile)
    kept: list[tuple[telegram.Post, str]] = []
    dropped: list[tuple[telegram.Post, str]] = []
    for p in posts:
        decision = screen.check(as_posting(p))
        (kept if decision.keep else dropped).append((p, decision.reason))

    # Survivors only: a dropped post is never evaluated, so fetching its page
    # would spend a stranger's bandwidth on something nobody will read.
    enriched = (
        telegram.fetch_externals([p for p, _ in kept], limit=MAX_ENRICHED) if enrich else 0
    )

    return {
        "channels": names,
        "collected": len(posts),
        "problems": [{"channel": c, "why": w} for c, w in problems],
        "kept": kept,
        "dropped": dropped,
        "filtered": True,
        "filtered_by": "profile" if search_profile else "starter",
        "enriched": enriched,
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
            # The post is where the vacancy was read; apply_url is where the
            # user acts. Both travel: the product never applies for them, so
            # losing the apply path would leave them to re-find it by hand.
            "apply_url": post.apply_url,
            "title": post.title,
            "company": f"@{post.channel}",
            "qualification": post.grade,
            "remote": post.remote,
            "salary_text": salary_label(post),
            "salary_from": post.salary_from,
            "salary_to": post.salary_to,
            "salary_currency": post.salary_currency,
            "skills": post.stack,
            "published": post.published,
            "source": "telegram",
            # Where the description was read, when the post only teased it. The
            # row's `url` stays the permalink — that is what the user opens —
            # and this says where the requirements actually came from.
            "description_url": post.description_url,
            # The post text is the vacancy: there is no page to fetch later, so
            # an evaluation has to read what was collected here. as_text() folds
            # in the linked description when one was read.
            "text": post.as_text(),
        })
    return rows
