"""Liveness and geekjob through the API: what the user sees and what they pay.

The network is replaced at `core.liveness.check` / `geekjob.collect`; no model
is called anywhere — the one test that reaches the paid step proves it is NOT
reached.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import Resume, User, Vacancy
from app.services import scan
from core import liveness
from core.sources import geekjob
from core.sources.habr_api import Posting
from tests.dbmark import requires_db

HABR_URL = "https://career.habr.com/vacancies/1000168684"


async def _resume_for(session, user: User) -> Resume:
    resume = Resume(
        user_id=user.id,
        filename="cv.md",
        raw_text="текст",
        profile={"target_role": "Backend"},
        search_profile={"queries": ["python"], "positive": ["python"]},
        is_active=True,
    )
    session.add(resume)
    await session.commit()
    return resume


async def _vacancy(session, user: User, url: str = HABR_URL, **kw) -> Vacancy:
    posting = Posting(id=url.rsplit("/", 1)[-1], url=url, title="Python разработчик",
                      company="Ромашка")
    await scan.persist(session, user.id, "habr", kept=[(posting, "")], dropped=[])
    await session.commit()
    row = (await session.execute(select(Vacancy).where(Vacancy.user_id == user.id))).scalar_one()
    for key, value in kw.items():
        setattr(row, key, value)
    await session.commit()
    return row


@requires_db
class TestClosedIsNotBought:
    async def test_evaluating_a_closed_vacancy_is_refused_before_the_model(
        self, client, auth_headers, session, user, monkeypatch
    ):
        await _resume_for(session, user)
        row = await _vacancy(session, user)
        monkeypatch.setattr(
            liveness, "check", lambda url, http=None: liveness.Liveness("closed", "архив")
        )

        def model_must_not_run(*a, **kw):
            raise AssertionError("платный шаг вызван для закрытой вакансии")

        from app.api import evaluations

        monkeypatch.setattr(evaluations, "evaluate", model_must_not_run)

        r = await client.post(f"/api/evaluations/{row.id}", headers=auth_headers)
        assert r.status_code == 409
        assert "закрыта" in r.json()["detail"]

        await session.refresh(row)
        await session.refresh(user)
        assert row.closed_at is not None and row.closed_reason == "архив"
        assert float(user.spent_rub) == 0

    async def test_an_unreachable_page_does_not_block_evaluation_as_closed(
        self, session, user, monkeypatch
    ):
        """Unknown is not closed: only positive evidence marks a vacancy."""
        from app.services import liveness as service

        row = await _vacancy(session, user)
        monkeypatch.setattr(
            liveness, "check", lambda url, http=None: liveness.Liveness("unknown", "503")
        )
        await service.refresh(row)
        assert row.closed_at is None and row.checked_at is not None

    async def test_a_reopened_vacancy_loses_its_mark(self, session, user, monkeypatch):
        from app.services import liveness as service

        row = await _vacancy(session, user)
        monkeypatch.setattr(
            liveness, "check", lambda url, http=None: liveness.Liveness("closed", "410")
        )
        await service.refresh(row)
        assert row.closed_at is not None
        monkeypatch.setattr(liveness, "check", lambda url, http=None: liveness.Liveness("open"))
        await service.refresh(row)
        assert row.closed_at is None and row.closed_reason is None


@requires_db
class TestRecheck:
    async def test_saved_rows_are_checked_and_the_closed_one_is_marked_not_hidden(
        self, client, auth_headers, session, user, monkeypatch
    ):
        await _resume_for(session, user)
        row = await _vacancy(session, user, is_saved=True)
        monkeypatch.setattr(
            liveness, "check", lambda url, http=None: liveness.Liveness("closed", "архив")
        )

        r = await client.post("/api/vacancies/recheck", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == {"checked": 1, "open": 0, "closed": 1, "unknown": 0}

        state = (await client.get("/api/state", headers=auth_headers)).json()
        [found] = state["found"]
        assert found["id"] == str(row.id)
        assert found["closed"]["reason"] == "архив"

    async def test_a_fresh_check_is_not_repeated(
        self, client, auth_headers, session, user, monkeypatch
    ):
        await _resume_for(session, user)
        await _vacancy(session, user, is_saved=True)
        monkeypatch.setattr(liveness, "check", lambda url, http=None: liveness.Liveness("open"))
        await client.post("/api/vacancies/recheck", headers=auth_headers)
        r = await client.post("/api/vacancies/recheck", headers=auth_headers)
        assert r.json()["checked"] == 0

    async def test_telegram_rows_are_not_asked(
        self, client, auth_headers, session, user, monkeypatch
    ):
        await _resume_for(session, user)
        await _vacancy(session, user, url="https://t.me/chan/1", is_saved=True)
        r = await client.post("/api/vacancies/recheck", headers=auth_headers)
        assert r.json()["checked"] == 0


@requires_db
class TestGeekjobScan:
    async def test_the_board_is_screened_by_the_profile_keywords(
        self, client, auth_headers, session, user, monkeypatch
    ):
        await _resume_for(session, user)
        cards = [
            Posting(id="a1", url="https://geekjob.ru/vacancy/a1", title="Python разработчик",
                    company="Ромашка"),
            Posting(id="b2", url="https://geekjob.ru/vacancy/b2", title="Специалист отдела кадров",
                    company="НАРТИС"),
        ]
        monkeypatch.setattr(geekjob, "collect", lambda pages: cards)

        r = await client.post(
            "/api/vacancies/scan", headers=auth_headers, json={"source": "geekjob", "pages": 1}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["collected"] == 2 and body["kept"] == 1 and body["dropped"] == 1

        rows = {v.external_id: v for v in (await session.execute(select(Vacancy))).scalars()}
        assert rows["a1"].stage == scan.SHORTLIST
        # The drop keeps its reason, as every free drop does.
        assert rows["b2"].stage == scan.SCREENED_OUT and rows["b2"].screen_reason

    async def test_a_broken_parser_is_an_error_not_an_empty_scan(
        self, client, auth_headers, session, user, monkeypatch
    ):
        await _resume_for(session, user)

        def broken(pages):
            raise geekjob.MarkupChanged("карточки не разобраны")

        monkeypatch.setattr(geekjob, "collect", broken)
        r = await client.post(
            "/api/vacancies/scan", headers=auth_headers, json={"source": "geekjob", "pages": 1}
        )
        assert r.status_code == 502
        assert "geekjob" in r.json()["detail"]


@requires_db
class TestCompanyScan:
    async def test_descriptions_are_fetched_for_survivors_only_and_stored(
        self, client, auth_headers, session, user, monkeypatch
    ):
        from core.sources import employers

        await _resume_for(session, user)
        cards = [
            Posting(id="yandex/yandex/1", url="https://yandex.ru/jobs/vacancies/a-1",
                    title="Python разработчик", company="Яндекс"),
            Posting(id="yandex/yandex/2", url="https://yandex.ru/jobs/vacancies/b-2",
                    title="Бухгалтер", company="Яндекс"),
        ]
        asked: list[str] = []

        def fake_collect(queries, groups):
            return employers.Collected(postings=cards)

        def fake_describe(postings):
            asked.extend(p.id for p in postings)
            return {p.id: f"полное описание {p.id}" for p in postings}

        monkeypatch.setattr(employers, "collect", fake_collect)
        monkeypatch.setattr(employers, "describe", fake_describe)

        r = await client.post(
            "/api/vacancies/scan", headers=auth_headers, json={"source": "company", "pages": 1}
        )
        assert r.status_code == 200, r.text
        # The page request is spent on the survivor only.
        assert asked == ["yandex/yandex/1"]

        rows = {v.external_id: v for v in (await session.execute(select(Vacancy))).scalars()}
        assert rows["yandex/yandex/1"].raw_text == "полное описание yandex/yandex/1"
        assert rows["yandex/yandex/2"].stage == scan.SCREENED_OUT
        assert rows["yandex/yandex/2"].raw_text is None
