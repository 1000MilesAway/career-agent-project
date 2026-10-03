"""One vacancy seen in several places is one vacancy.

The pure half (keys, fingerprints) needs no database; the second half checks
that the pool links a copy to its original, that a user gets the vacancy
once, and that the copy is still named in the discard log — never thrown away.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select

from app.models import Posting as PooledPosting
from app.models import Resume, User, Vacancy
from app.services import scan
from core import dedup
from core.sources import jobposting
from core.sources.habr_api import Posting
from tests.dbmark import requires_db

FIXTURES = Path(__file__).parent / "fixtures"


def posting(pid: str, title: str = "Python разработчик", company: str = "ООО Ромашка") -> Posting:
    return Posting(id=pid, url=f"https://career.habr.com/vacancies/{pid}", title=title,
                   company=company)


def description() -> str:
    html = (FIXTURES / "geekjob_vacancy.html").read_text(encoding="utf-8")
    return jobposting.parse(html).description


class TestKeys:
    def test_legal_form_and_punctuation_do_not_split_a_company(self):
        a = dedup.posting_key(posting("1", company="ООО «Ромашка»"))
        b = dedup.posting_key(posting("2", company="Ромашка, АО"))
        assert a and a == b

    def test_a_latin_lookalike_does_not_split_a_company(self):
        """"Aгентство" with a Latin A is on geekjob as typed by hand."""
        a = dedup.company_key("Aгентство Martsun & Partners")
        b = dedup.company_key("Агентство Martsun & Partners")
        assert a == b

    def test_latin_words_stay_latin(self):
        assert dedup.title_key("Python developer") == "python developer"

    def test_the_grade_keeps_two_openings_apart(self):
        a = dedup.posting_key(posting("1", title="Senior Python разработчик"))
        b = dedup.posting_key(posting("2", title="Middle Python разработчик"))
        assert a != b

    def test_a_telegram_channel_is_not_a_company(self):
        """Two channels with a "Python developer" each are not one job."""
        assert dedup.posting_key(posting("1", company="@pythonrabota")) == ""

    def test_links_to_our_sources_are_found(self):
        text = (
            "Откликнуться: https://career.habr.com/vacancies/1000168684?utm=tg\n"
            "Или тут: geekjob.ru/vacancy/6ab2b32973abaf8df70aa37d"
        )
        assert dedup.linked_vacancies(text) == [
            ("habr", "1000168684"),
            ("geekjob", "6ab2b32973abaf8df70aa37d"),
        ]


class TestFingerprint:
    def test_a_repost_with_its_own_wrapper_matches(self):
        body = description()
        a = dedup.fingerprint("🔥 Вакансия от @channel_one\n" + body)
        b = dedup.fingerprint("#вакансия #python репост\n" + body + "\nПодписывайтесь на канал!")
        assert dedup.similarity(a, b) >= dedup.SIMILARITY_THRESHOLD

    def test_a_different_vacancy_does_not(self):
        other = (FIXTURES / "habr_open.html").read_text(encoding="utf-8")
        a = dedup.fingerprint(description())
        b = dedup.fingerprint(other)
        assert dedup.similarity(a, b) < dedup.SIMILARITY_THRESHOLD

    def test_short_text_is_not_fingerprinted(self):
        """Too little text matches boilerplate, not vacancies."""
        assert dedup.fingerprint("Python разработчик, удалёнка, пишите @hr") == ""
        assert dedup.similarity("", "") == 0.0


async def _user(session) -> User:
    user = User(email=f"u{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    session.add(
        Resume(user_id=user.id, filename="cv.md", raw_text="т", profile={}, is_active=True)
    )
    await session.flush()
    return user


async def _rows(session, user) -> dict[tuple[str, str], Vacancy]:
    result = await session.execute(select(Vacancy).where(Vacancy.user_id == user.id))
    return {(v.source, v.external_id): v for v in result.scalars()}


async def _pooled(session) -> dict[tuple[str, str], PooledPosting]:
    result = await session.execute(select(PooledPosting))
    return {(p.source, p.external_id): p for p in result.scalars()}


PROFILE = {"queries": ["python"], "positive": ["Python", "Backend"]}


@requires_db
class TestPersistMarksCopies:
    async def test_the_same_vacancy_on_another_board_is_pooled_as_a_copy(self, session):
        user = await _user(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("100"), "")], dropped=[])
        counts = await scan.persist(
            session, user.id, "geekjob",
            kept=[(posting("abc", company="Ромашка"), "")], dropped=[],
        )
        await session.commit()

        pooled = await _pooled(session)
        assert pooled[("geekjob", "abc")].duplicate_of_id == pooled[("habr", "100")].id
        # The user gets the vacancy once; the copy is counted, not hidden.
        rows = await _rows(session, user)
        assert counts["duplicates"] == 1
        assert rows[("habr", "100")].stage == scan.SHORTLIST
        assert ("geekjob", "abc") not in rows

    async def test_the_copy_is_named_in_the_discard_log(self, session):
        """Not silent: the copy is listed with the vacancy it copies."""
        user = await _user(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("100"), "")], dropped=[])
        await scan.persist(
            session, user.id, "geekjob",
            kept=[(posting("abc", company="Ромашка"), "")], dropped=[],
        )
        await session.commit()

        screened = await scan.explain_screened(session, user.id, PROFILE)
        reasons = {row["source"]: row["reason"] for row in screened}
        assert "дубль" in reasons["geekjob"] and "Хабр" in reasons["geekjob"]

    async def test_two_openings_on_one_board_are_not_duplicates(self, session):
        """Same company, same title, two ids: two teams hiring, not a copy."""
        user = await _user(session)
        await scan.persist(
            session, user.id, "habr",
            kept=[(posting("1"), ""), (posting("2"), "")], dropped=[],
        )
        await session.commit()
        rows = await _rows(session, user)
        assert len(rows) == 2
        assert all(v.stage == scan.SHORTLIST for v in rows.values())

    async def test_a_telegram_post_linking_to_habr_is_that_vacancy(self, session):
        user = await _user(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1000168684"), "")], dropped=[])
        tg = Posting(id="chan/7", url="https://t.me/chan/7", title="Python", company="@chan")
        text = "Ищем Python разработчика. Отклик: https://career.habr.com/vacancies/1000168684"
        await scan.persist(
            session, user.id, "telegram", kept=[(tg, "")], dropped=[], texts={"chan/7": text}
        )
        await session.commit()
        pooled = await _pooled(session)
        assert pooled[("telegram", "chan/7")].duplicate_of_id == pooled[("habr", "1000168684")].id

    async def test_a_repost_in_another_channel_is_caught_by_its_text(self, session):
        user = await _user(session)
        body = description()
        a = Posting(id="one/1", url="https://t.me/one/1", title="Senior Backend", company="@one")
        b = Posting(id="two/9", url="https://t.me/two/9", title="Senior Backend", company="@two")
        await scan.persist(
            session, user.id, "telegram",
            kept=[(a, ""), (b, "")], dropped=[],
            texts={"one/1": "🔥 @one\n" + body, "two/9": "#вакансия\n" + body + "\n@two"},
        )
        await session.commit()
        pooled = await _pooled(session)
        assert pooled[("telegram", "two/9")].duplicate_of_id == pooled[("telegram", "one/1")].id

    async def test_a_copy_points_at_the_root_not_at_another_copy(self, session):
        user = await _user(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await scan.persist(
            session, user.id, "geekjob", kept=[(posting("x", company="Ромашка"), "")], dropped=[]
        )
        await scan.persist(
            session, user.id, "company", kept=[(posting("c", company="АО Ромашка"), "")],
            dropped=[],
        )
        await session.commit()
        pooled = await _pooled(session)
        assert pooled[("company", "c")].duplicate_of_id == pooled[("habr", "1")].id

    async def test_a_dropped_original_does_not_hide_a_copy_that_passes(self, session):
        """The user never saw the dropped one; marking the copy would hide both."""
        user = await _user(session)
        await scan.persist(
            session, user.id, "habr", kept=[], dropped=[(posting("1"), "грейд ниже нужного")]
        )
        await scan.persist(
            session, user.id, "geekjob", kept=[(posting("x", company="Ромашка"), "")], dropped=[]
        )
        await session.commit()
        rows = await _rows(session, user)
        assert rows[("geekjob", "x")].stage == scan.SHORTLIST

    async def test_another_user_whose_filters_drop_the_original_sees_the_copy(self, session):
        """Dedup is pooled, visibility is per user."""
        first, second = await _user(session), await _user(session)
        await scan.persist(session, first.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await scan.persist(
            session, first.id, "geekjob", kept=[(posting("x", company="Ромашка"), "")],
            dropped=[],
        )
        await scan.persist(
            session, second.id, "habr", kept=[], dropped=[(posting("1"), "не удалённая работа")]
        )
        await scan.persist(
            session, second.id, "geekjob", kept=[(posting("x", company="Ромашка"), "")],
            dropped=[],
        )
        await session.commit()
        assert ("geekjob", "x") not in await _rows(session, first)
        assert (await _rows(session, second))[("geekjob", "x")].stage == scan.SHORTLIST

    async def test_a_rescan_keeps_a_duplicate_a_duplicate(self, session):
        """Otherwise the next scan queues a second triage of the same vacancy."""
        user = await _user(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        copy = posting("x", company="Ромашка")
        await scan.persist(session, user.id, "geekjob", kept=[(copy, "")], dropped=[])
        await session.commit()
        counts = await scan.persist(session, user.id, "geekjob", kept=[(copy, "")], dropped=[])
        await session.commit()
        rows = await _rows(session, user)
        assert ("geekjob", "x") not in rows
        assert counts["duplicates"] == 1
