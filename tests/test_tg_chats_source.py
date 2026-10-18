"""Telegram chats through the tg-provider userbot (source "telethon").

The provider is replaced by a fake `httpx.get` serving a recorded answer
with contacts replaced; nothing leaves the process. The scan tests at the
bottom need Postgres and skip without it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.models import Posting as PooledPosting
from app.services import pool, scan
from core.filters import SearchProfile
from core.sources import tg_chats
from tests.dbmark import requires_db

ANSWER = json.loads(
    (Path(__file__).parent / "fixtures" / "tg_provider" / "posts.json").read_text("utf-8")
)
NOW = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("TG_PROVIDER_URL", "http://provider:8091/")
    monkeypatch.setenv("TG_PROVIDER_TOKEN", "secret")
    calls: list[tuple[str, object]] = []
    # A scan finished just now: the recorded date would read as a stopped
    # scan once the test runs more than STALE_AFTER after it was recorded.
    fresh = {**ANSWER, "status": {**ANSWER["status"], "finished_at": datetime.now(UTC).isoformat()}}
    answers = {"/posts": httpx.Response(200, json=fresh)}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        calls.append((url, params) if method == "GET" else (method, url, json))
        assert headers == {"Authorization": "Bearer secret"}
        path = url.removeprefix("http://provider:8091")
        return answers.get(path, httpx.Response(404, text="not found"))

    monkeypatch.setattr(tg_chats.httpx, "request", fake_request)
    return calls, answers


def test_not_configured_is_off_not_broken(monkeypatch):
    monkeypatch.delenv("TG_PROVIDER_URL", raising=False)
    monkeypatch.delenv("TG_PROVIDER_TOKEN", raising=False)
    assert tg_chats.configured() is False
    assert "telethon" not in scan.active_sources()
    with pytest.raises(ValueError, match="Telegram-чаты не подключены"):
        scan.plan("telethon", SearchProfile(positive=["qa"]), scan.Options())


def test_configured_joins_the_scan_of_everything(provider):
    assert "telethon" in scan.active_sources()


def test_only_vacancies_are_kept_once_each(provider):
    calls, _ = provider
    posts, problems, chats = tg_chats.collect()
    assert calls == [("http://provider:8091/posts", {"days": tg_chats.DAYS})]
    assert chats == ["hr_breakfast_emergency"]
    # The CV is not a vacancy; the QA ad posted twice is one posting.
    assert [p.id for p in posts] == [
        "hr_breakfast_emergency/104409",
        "hr_breakfast_emergency/104410",
        "private_group/7",
    ]
    qa = posts[1]
    assert qa.title == "QA Engineer (Middle), удалённо"
    assert qa.remote is True
    assert (qa.salary_from, qa.salary_to, qa.salary_currency) == (150_000, 200_000, "RUB")
    assert qa.url == "https://t.me/hr_breakfast_emergency/104410"


def test_a_teaser_link_in_plain_text_is_found():
    post = tg_chats.as_post(ANSWER["posts"][0])
    assert post is not None
    assert post.description_url == "https://telegra.ph/SEO-kopirajter-10-15"


def test_a_private_group_message_has_no_made_up_link():
    post = tg_chats.as_post(ANSWER["posts"][4])
    assert post is not None
    assert post.url == ""
    assert post.id == "private_group/7"


def test_a_fresh_scan_reports_nothing():
    assert tg_chats._problems(ANSWER["status"], NOW) == []


def test_a_stopped_scan_is_named_not_served_as_today():
    status = {"finished_at": "2026-10-12T06:21:00+00:00", "chats": []}
    assert tg_chats._problems(status, NOW) == [
        ("tg-provider", "чаты не сканировались с 2026-10-12 06:21 UTC")
    ]


def test_a_failed_scan_and_a_failed_chat_are_named():
    status = {
        "finished_at": "2026-10-15T06:21:00+00:00",
        "error": "AuthKeyUnregisteredError",
        "chats": [{"chat": "myjobit", "error": "ChannelPrivateError"}],
    }
    assert tg_chats._problems(status, NOW) == [
        ("tg-provider", "последний скан чатов упал: AuthKeyUnregisteredError"),
        ("myjobit", "@myjobit: ChannelPrivateError"),
    ]


def test_provider_errors_are_errors_not_empty_lists(provider, monkeypatch):
    _, answers = provider
    answers["/posts"] = httpx.Response(401, text="unauthorized")
    with pytest.raises(tg_chats.ProviderError, match="401"):
        tg_chats.collect()

    def down(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(tg_chats.httpx, "request", down)
    with pytest.raises(tg_chats.ProviderError, match="не ответил"):
        tg_chats.collect()


def test_set_chat_puts_the_quoted_name(provider):
    calls, answers = provider
    answers["/chats/https%3A%2F%2Ft.me%2Fru_python"] = httpx.Response(
        200, json={"chat": "ru_python", "enabled": True, "added_at": "2026-10-16T06:00:00+00:00"}
    )
    item = tg_chats.set_chat("https://t.me/ru_python", True)
    assert item["chat"] == "ru_python"
    assert calls[-1] == (
        "PUT", "http://provider:8091/chats/https%3A%2F%2Ft.me%2Fru_python", {"enabled": True}
    )


def test_plan_reads_the_chats_whole_and_needs_keywords(provider):
    plan = scan.plan("telethon", SearchProfile(positive=["qa"]), scan.Options())
    assert len(plan.listings) == 1
    with pytest.raises(ValueError, match="нет ключевых слов"):
        scan.plan("telethon", SearchProfile(queries=["qa"]), scan.Options())


def test_fetch_stores_text_and_marks_teasers(provider):
    batch = scan.fetch_telethon()
    assert batch.source == "telethon"
    by_id = {s.posting.id: s for s in batch.sightings}
    teaser = by_id["hr_breakfast_emergency/104409"]
    assert teaser.needs_text is True
    assert teaser.text and "SEO" in teaser.text
    # The chat is not the employer: it stays in the id, and this teaser names none.
    assert teaser.posting.company == ""
    assert batch.extra["problems"] == []


def test_listing_is_read_at_most_hourly():
    assert pool.FRESH_FOR["telethon"].total_seconds() == 3600


async def _user(session):
    import uuid

    from app.models import User

    user = User(email=f"u{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


@requires_db
class TestScanTelethon:
    async def test_chats_land_in_the_pool_under_their_own_source(
        self, session, monkeypatch, provider
    ):
        # The teaser's description page is someone else's host: not read here.
        monkeypatch.setattr(scan, "_read_descriptions", lambda source, rows: {})
        user = await _user(session)
        result = await scan.collect(
            session, user.id, {"queries": ["qa"], "positive": ["qa"]}, ("telethon",)
        )
        await session.commit()
        found = result["sources"]["telethon"]
        # SEO copywriter is not "qa"; the same company's QA Lead from the
        # private group is the QA ad again, reached once.
        assert (found["new"], found["dropped"], found["duplicates"]) == (1, 1, 1)
        pooled = (await session.execute(
            select(PooledPosting).where(PooledPosting.source == "telethon")
        )).scalars().all()
        assert {p.external_id for p in pooled} == {
            "hr_breakfast_emergency/104409",
            "hr_breakfast_emergency/104410",
            "private_group/7",
        }
        assert all(p.raw_text for p in pooled)

    async def test_provider_failure_is_reported_by_name(self, session, provider):
        _, answers = provider
        answers["/posts"] = httpx.Response(502, text="bad gateway")
        user = await _user(session)
        result = await scan.collect(
            session, user.id, {"queries": ["qa"], "positive": ["qa"]}, ("telethon",)
        )
        assert "502" in result["sources"]["telethon"]["error"]
