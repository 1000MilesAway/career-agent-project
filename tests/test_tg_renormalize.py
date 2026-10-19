"""Pooled Telegram posts get today's title and company without losing their text."""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select

from app.models import Posting
from app.services import tg_renormalize
from core.sources import telegram
from tests.dbmark import requires_db

# How a channel post was stored before 2026-10-19: a line per HTML node.
SPLIT = "Expert Illustrator в\nCD Project RED\n:\nhttps://remocate.app/jobs/1"
WIDGET = (
    '<div class="tgme_widget_message" data-post="evacuatejobs/6667">'
    '<div class="tgme_widget_message_text">Expert Illustrator в '
    '<a href="https://remocate.app/jobs/1">CD Project RED</a>: удалённо</div></div>'
)


def test_fetch_post_reads_the_widget():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=WIDGET)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        text, hrefs = telegram.fetch_post("https://t.me/evacuatejobs/6667", client)
    assert seen == ["https://t.me/evacuatejobs/6667?embed=1&mode=tme"]
    assert text == "Expert Illustrator в CD Project RED: удалённо"
    assert hrefs == ["https://remocate.app/jobs/1"]


def test_fetch_post_gives_none_for_a_deleted_post():
    page = '<div class="tgme_widget_message_error">Post not found</div>'
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page))) as c:
        assert telegram.fetch_post("https://t.me/x/1", c) is None


def test_own_text_drops_the_read_description():
    post = telegram.Post(
        id="c/1", url="", channel="c", text="Go Developer",
        description_url="https://telegra.ph/x", description_text="Требования",
    )
    assert telegram.own_text(post.full_text()) == "Go Developer"


def _row(source: str, external_id: str, raw_text: str, title: str) -> Posting:
    return Posting(
        id=uuid.uuid4(), source=source, external_id=external_id, raw_text=raw_text,
        title=title, url=f"https://t.me/{external_id}",
    )


@requires_db
async def test_dry_run_writes_nothing(session):
    session.add(_row("telethon", "chat/1", "Golang-разработчик в SimbirSoft\nУдалённо", "old"))
    await session.commit()

    counts, changes = await tg_renormalize.run(session)
    await session.commit()

    assert counts["changed"] == 1
    assert (changes[0].new_title, changes[0].new_company) == ("Golang-разработчик", "SimbirSoft")
    row = (await session.scalars(select(Posting))).one()
    assert (row.title, row.company) == ("old", None)


@requires_db
async def test_no_company_as_null_or_empty_is_no_change(session):
    session.add(_row("telethon", "chat/2", "Go Developer\nУдалённо", "Go Developer"))
    await session.commit()
    counts, _ = await tg_renormalize.run(session)
    assert counts.get("changed", 0) == 0


@requires_db
async def test_apply_refetches_channel_posts_and_keeps_the_text(session):
    session.add(_row("telegram", "evacuatejobs/6667", SPLIT, "Expert Illustrator в"))
    session.add(_row("telegram", "evacuatejobs/1", "Go Developer\nКомпания: Acme", "Go"))
    await session.commit()

    def fetch(url: str):
        # The second post is gone from Telegram: its stored text is parsed.
        text = "Expert Illustrator в CD Project RED: https://remocate.app/jobs/1"
        return (text, []) if "6667" in url else None

    counts, _ = await tg_renormalize.run(
        session, refetch=True, apply=True, fetch=fetch, delay=0
    )
    await session.commit()

    rows = {r.external_id: r for r in await session.scalars(select(Posting))}
    illustrator = rows["evacuatejobs/6667"]
    assert (illustrator.title, illustrator.company) == ("Expert Illustrator", "CD Project RED")
    assert illustrator.raw_text == SPLIT
    assert illustrator.dedup_key
    assert (rows["evacuatejobs/1"].title, rows["evacuatejobs/1"].company) == ("Go Developer", "Acme")
    assert counts["refetched"] == 1 and counts["refetch_failed"] == 1
