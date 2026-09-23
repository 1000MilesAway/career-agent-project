"""One scan over every source: failures isolated, writes in a fixed order.

The fetch functions are replaced; nothing leaves the process and no model is
called.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import Resume, User, Vacancy
from app.services import scan
from core.sources.habr_api import Posting
from tests.dbmark import requires_db

PROFILE = {"queries": ["python"], "positive": ["python"]}


def batch(source: str, *postings: Posting, texts: dict | None = None) -> scan.Batch:
    return scan.Batch(source, len(postings), [(p, "") for p in postings], [], texts or {})


def fakes(monkeypatch, **batches):
    """Replace every fetch; a value that is an exception is raised instead."""

    def make(name):
        def fetch(*args, **kwargs):
            value = batches.get(name, batch(name))
            if isinstance(value, BaseException):
                raise value
            return value

        return fetch

    monkeypatch.setattr(scan, "fetch_habr", make("habr"))
    monkeypatch.setattr(scan, "fetch_employers", make("company"))
    monkeypatch.setattr(scan, "fetch_geekjob", make("geekjob"))
    monkeypatch.setattr(scan, "fetch_telegram", make("telegram"))


async def _user(session) -> User:
    import uuid

    user = User(email=f"u{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


@requires_db
class TestCollectAll:
    async def test_a_failing_source_is_reported_and_the_others_still_land(
        self, session, monkeypatch
    ):
        user = await _user(session)
        fakes(
            monkeypatch,
            habr=batch("habr", Posting(id="1", url="u", title="Python", company="A")),
            geekjob=RuntimeError("geekjob лежит"),
        )
        result = await scan.collect_all(session, user.id, PROFILE)
        await session.commit()

        assert "geekjob лежит" in result["sources"]["geekjob"]["error"]
        assert result["sources"]["habr"]["new"] == 1
        assert result["new"] == 1
        rows = (await session.execute(select(Vacancy))).scalars().all()
        assert [r.source for r in rows] == ["habr"]

    async def test_habr_is_the_original_and_telegram_the_copy(self, session, monkeypatch):
        """Write order decides which copy the user keeps: the one with fields."""
        user = await _user(session)
        tg = Posting(id="chan/1", url="https://t.me/chan/1", title="Python", company="@chan")
        fakes(
            monkeypatch,
            habr=batch(
                "habr",
                Posting(id="1000168684", url="https://career.habr.com/vacancies/1000168684",
                        title="Python разработчик", company="Ромашка"),
            ),
            telegram=batch(
                "telegram", tg,
                texts={"chan/1": "Отклик: https://career.habr.com/vacancies/1000168684"},
            ),
        )
        result = await scan.collect_all(session, user.id, PROFILE)
        await session.commit()

        rows = {r.source: r for r in (await session.execute(select(Vacancy))).scalars()}
        assert rows["habr"].stage == scan.SHORTLIST
        assert rows["telegram"].duplicate_of_id == rows["habr"].id
        assert result["duplicates"] == 1

    async def test_no_profile_fails_once_not_four_times(self, session, monkeypatch):
        import pytest

        user = await _user(session)
        fakes(monkeypatch)
        with pytest.raises(ValueError):
            await scan.collect_all(session, user.id, None)


@requires_db
class TestScanAllEndpoint:
    async def test_the_cabinet_request_needs_no_pages(
        self, client, auth_headers, session, user, monkeypatch
    ):
        session.add(
            Resume(user_id=user.id, filename="cv.md", raw_text="т", profile={},
                   search_profile=PROFILE, is_active=True)
        )
        await session.commit()
        fakes(monkeypatch)

        r = await client.post(
            "/api/vacancies/scan", headers=auth_headers,
            json={"source": "all", "include_global": True},
        )
        assert r.status_code == 200, r.text
        assert set(r.json()["sources"]) == {"habr", "company", "geekjob", "telegram"}

    async def test_include_global_reaches_the_employer_boards(
        self, client, auth_headers, session, user, monkeypatch
    ):
        session.add(
            Resume(user_id=user.id, filename="cv.md", raw_text="т", profile={},
                   search_profile=PROFILE, is_active=True)
        )
        await session.commit()
        fakes(monkeypatch)
        seen = {}

        def employers(search_profile, groups=("ru",)):
            seen["groups"] = groups
            return batch("company")

        monkeypatch.setattr(scan, "fetch_employers", employers)
        await client.post(
            "/api/vacancies/scan", headers=auth_headers,
            json={"source": "all", "include_global": True},
        )
        assert seen["groups"] == ("ru", "global")
