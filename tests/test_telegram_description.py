"""A teaser's linked description has to survive all the way into raw_text.

This is the step where the feature is easiest to lose silently. The collector
can read telegra.ph perfectly and the evaluation still judge four lines, because
`collect_telegram` stores the posting body separately from the rows — and
storing `post.text` there would drop the fetched page at the last moment.

Telegram is the one source with no page to fetch later: whatever is not stored
here can never be recovered, so a wrong call is invisible until a user wonders
why a good vacancy scored badly.

No network: `telegram.search_many` and the fetcher are stubbed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import Vacancy
from app.services import scan
from core.sources import telegram
from tests.dbmark import requires_db

TEASER_TEXT = (
    "Python Backend AI-агенты\nСмотреть вакансию\nФормат: удалённо\n"
    "Вакансия открыта, писать @hr"
)
DESCRIPTION = (
    "Обязательные требования\nBackend-разработка от 5 лет, из них часть на Python.\n"
    "Микросервисы, PostgreSQL, Docker, Kubernetes."
)


def teaser(pid: str = "chan/2340") -> telegram.Post:
    return telegram.Post(
        id=pid,
        url=f"https://t.me/{pid}",
        channel="chan",
        title="Python Backend AI-агенты",
        text=TEASER_TEXT,
        description_url="https://telegra.ph/Python-Backend-09-17",
        published="2026-09-18T10:00:00+00:00",
    )


@pytest.fixture
def stub_collector(monkeypatch):
    """One teaser from the channel, and a fetcher that always succeeds."""
    def _set(posts, fetched: str | None = DESCRIPTION):
        monkeypatch.setattr(
            telegram, "search_many", lambda *a, **k: (list(posts), [])
        )

        def fake_fetch(targets, limit=None, client=None):
            if fetched is None:
                return 0
            filled = 0
            for p in targets:
                if p.description_url and not p.description_text:
                    p.description_text = fetched
                    filled += 1
            return filled

        monkeypatch.setattr(telegram, "fetch_externals", fake_fetch)
    return _set


PROFILE = {"queries": ["python"], "positive": ["python", "backend"]}


async def _stored_text(session, user_id: uuid.UUID) -> str | None:
    row = (
        await session.execute(
            select(Vacancy).where(Vacancy.user_id == user_id, Vacancy.source == "telegram")
        )
    ).scalars().first()
    return row.raw_text if row else None


@requires_db
class TestDescriptionReachesStorage:
    async def test_the_linked_description_is_stored(self, session, user, stub_collector):
        """Storing post.text instead would throw the fetched page away here."""
        stub_collector([teaser()])
        result = await scan.collect_telegram(
            session, user.id, PROFILE, channels=["chan"]
        )
        assert result["enriched"] == 1

        text = await _stored_text(session, user.id)
        assert text is not None
        assert "Backend-разработка от 5 лет" in text
        # The post's own lines stay: they carry the format and the contact.
        assert "Python Backend AI-агенты" in text
        # And the reader can tell which half came from where.
        assert "telegra.ph" in text

    async def test_a_failed_fetch_still_stores_the_post(self, session, user, stub_collector):
        """A description we could not read must not cost us the vacancy."""
        stub_collector([teaser()], fetched=None)
        result = await scan.collect_telegram(
            session, user.id, PROFILE, channels=["chan"]
        )
        assert result["enriched"] == 0

        text = await _stored_text(session, user.id)
        assert text is not None
        assert "Python Backend AI-агенты" in text
        assert "Backend-разработка" not in text

    async def test_a_plain_post_is_stored_unchanged(self, session, user, stub_collector):
        """No link, no fetch, no label bolted onto the text."""
        plain = teaser()
        plain.description_url = ""
        plain.text = (
            "Python Backend-разработчик\nИщем инженера в команду. Требования: "
            "Python от трёх лет, PostgreSQL, Docker. Удалённо. Писать @hr"
        )
        stub_collector([plain])
        await scan.collect_telegram(session, user.id, PROFILE, channels=["chan"])

        text = await _stored_text(session, user.id)
        assert text == plain.text
        assert "Полное описание" not in text
