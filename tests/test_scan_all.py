"""One scan over every source: failures isolated, writes in a fixed order,
and a listing read recently is answered from the shared pool.

The fetch functions are replaced; nothing leaves the process and no model is
called.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import Posting as PooledPosting
from app.models import Resume, User, Vacancy
from app.services import scan
from app.services.pool import Sighting
from core.sources.habr_api import Posting
from tests.dbmark import requires_db

PROFILE = {"queries": ["python"], "positive": ["python"]}


def batch(source: str, *postings: Posting, texts: dict | None = None) -> scan.Batch:
    texts = texts or {}
    return scan.Batch(source, [Sighting(p, texts.get(p.id)) for p in postings])


def fakes(monkeypatch, calls: list | None = None, **batches):
    """Replace every fetch; a value that is an exception is raised instead."""

    def make(name):
        def fetch(*args, **kwargs):
            if calls is not None:
                calls.append(name)
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
        assert "telegram" not in rows
        pooled = {p.source: p for p in (await session.execute(select(PooledPosting))).scalars()}
        assert pooled["telegram"].duplicate_of_id == pooled["habr"].id
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

        def employers(queries, group):
            seen.setdefault("groups", []).append(group)
            return batch("company")

        monkeypatch.setattr(scan, "fetch_employers", employers)
        await client.post(
            "/api/vacancies/scan", headers=auth_headers,
            json={"source": "all", "include_global": True},
        )
        assert seen["groups"] == ["ru", "global"]


@requires_db
class TestThePoolSavesRequests:
    """A listing read recently is not read again — by anyone."""

    async def test_a_second_user_is_matched_from_the_pool(self, session, monkeypatch):
        first, second = await _user(session), await _user(session)
        calls: list[str] = []
        fakes(
            monkeypatch, calls,
            habr=batch("habr", Posting(id="1", url="u", title="Python", company="A")),
        )
        await scan.collect_all(session, first.id, PROFILE)
        await session.commit()
        assert sorted(calls) == ["company", "geekjob", "habr", "telegram"]

        calls.clear()
        result = await scan.collect_all(session, second.id, PROFILE)
        await session.commit()

        assert calls == []
        assert result["new"] == 1
        assert result["sources"]["habr"]["from_pool"] == 1
        rows = (await session.execute(
            select(Vacancy).where(Vacancy.user_id == second.id)
        )).scalars().all()
        assert [r.title for r in rows] == ["Python"]

    async def test_a_stale_listing_is_read_again(self, session, monkeypatch):
        from datetime import timedelta

        from app.models import SourceFetch

        user = await _user(session)
        calls: list[str] = []
        fakes(monkeypatch, calls)
        await scan.collect_all(session, user.id, PROFILE)
        await session.commit()

        for fetch in (await session.execute(select(SourceFetch))).scalars():
            fetch.fetched_at -= timedelta(hours=3)
        await session.commit()

        calls.clear()
        await scan.collect_all(session, user.id, PROFILE)
        assert sorted(calls) == ["company", "geekjob", "habr", "telegram"]

    async def test_a_new_query_is_fetched_while_the_known_one_is_not(
        self, session, monkeypatch
    ):
        user = await _user(session)
        asked: list[list[str]] = []

        def habr(queries, **kw):
            asked.append(queries)
            return batch("habr")

        fakes(monkeypatch)
        monkeypatch.setattr(scan, "fetch_habr", habr)
        await scan.collect_habr(session, user.id, PROFILE)
        await scan.collect_habr(session, user.id, {**PROFILE, "queries": ["python", "django"]})
        assert asked == [["python"], ["django"]]

    async def test_a_drop_for_one_user_is_a_keep_for_another(self, session, monkeypatch):
        """The pool is unscreened; each user's filters run over it."""
        first, second = await _user(session), await _user(session)
        fakes(
            monkeypatch,
            habr=batch(
                "habr",
                Posting(id="1", url="u1", title="Python разработчик", company="A"),
                Posting(id="2", url="u2", title="Go разработчик", company="B"),
            ),
        )
        await scan.collect_habr(session, first.id, PROFILE)
        await scan.collect_habr(
            session, second.id, {"queries": ["python"], "positive": ["go"]}
        )
        await session.commit()

        async def titles(user):
            rows = await session.execute(select(Vacancy).where(Vacancy.user_id == user.id))
            return sorted(r.title for r in rows.scalars())

        assert await titles(first) == ["Python разработчик"]
        assert await titles(second) == ["Go разработчик"]
