"""Re-derive the title and company of pooled Telegram posts with today's parser.

A posting's listing fields are refreshed only when a scan sees it again, and a
channel preview shows its latest ~20 posts: everything older keeps whatever
the parser of its day made of it. When the parser improves, this brings the
pool up to it (docs/decisions.md, 2026-10-19).

Only `title`, `company` and the `dedup_key` built from them change. The text
is never rewritten — it is what evaluations were bought on (app/services/pool.py
sets it once). Channel posts were stored with a line break between every HTML
node, so with `refetch` their text is read again from the post's widget page
for the parse only; a post that cannot be read falls back to the stored text.
Chat posts (`telethon`) arrive as plain text and are parsed as stored.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import dedup
from core.sources import telegram
from core.tg_scan import as_posting

from ..models import Posting

SOURCES = ("telegram", "telethon")


@dataclass
class Change:
    external_id: str
    old_title: str
    new_title: str
    old_company: str
    new_company: str


def reparse(row: Posting, fetched: tuple[str, list[str]] | None) -> telegram.Post:
    """The post as the current parser reads it."""
    text, hrefs = fetched or (telegram.own_text(row.raw_text or ""), [])
    channel = row.external_id.split("/", 1)[0]
    return telegram.build_post(channel, row.external_id, text, "", hrefs)


async def run(
    session: AsyncSession,
    *,
    refetch: bool = False,
    apply: bool = False,
    fetch: Callable[[str], tuple[str, list[str]] | None] = telegram.fetch_post,
    delay: float = telegram.DELAY_SECONDS,
) -> tuple[dict[str, int], list[Change]]:
    """Counts and the changes; writes them only with `apply` (the caller commits)."""
    rows = (
        await session.scalars(
            select(Posting).where(Posting.source.in_(SOURCES)).order_by(Posting.created_at)
        )
    ).all()
    counts: Counter[str] = Counter(total=len(rows))
    changes: list[Change] = []
    for row in rows:
        fetched = None
        if refetch and row.source == "telegram" and row.url:
            fetched = fetch(row.url)
            counts["refetched" if fetched else "refetch_failed"] += 1
            time.sleep(delay)
        post = reparse(row, fetched)
        # `title` is NOT NULL, and an old title beats none.
        post.title = post.title or row.title
        company = post.company or None
        counts["company_before"] += bool(row.company)
        counts["company_after"] += bool(company)
        if (post.title, company) == (row.title, row.company):
            continue
        counts["changed"] += 1
        changes.append(
            Change(row.external_id, row.title, post.title, row.company or "", company or "")
        )
        if apply:
            row.title = post.title
            row.company = company
            row.dedup_key = dedup.posting_key(as_posting(post)) or None
    return dict(counts), changes
