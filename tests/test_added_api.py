"""«Мои вакансии»: a vacancy the user brings by link or text.

The rules under test are the ones a rewrite would lose: adding is free and
never duplicates a row the user has; nothing a scan does removes an added
row; a pasted text stays the user's own and leaves with them.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import Posting, Resume, Vacancy
from core.sources import user_added
from core.sources.habr_api import Posting as Listing
from tests.dbmark import requires_db
from tests.pool_helpers import make_vacancy

TEXT = """Аналитик данных
Компания: ООО Ромашка
Грейд: Middle
Строить отчёты в SQL и Python, считать продуктовые метрики, помогать командам
принимать решения. Удалённо, зарплата от 200 000 ₽.
"""


async def _add(client, headers, source: str):
    return await client.post("/api/vacancies/add", json={"source": source}, headers=headers)


async def _found(client, headers) -> list[dict]:
    return (await client.get("/api/state", headers=headers)).json()["found"]


@requires_db
class TestAdd:
    async def test_a_pasted_text_becomes_an_added_row(self, client, auth_headers):
        r = await _add(client, auth_headers, TEXT)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] is True
        assert body["vacancy"]["title"] == "Аналитик данных"
        assert body["vacancy"]["source"] == "text"

        [row] = await _found(client, auth_headers)
        assert row["added_at"] is not None
        assert row["company"] == "ООО Ромашка"

    async def test_the_same_text_twice_is_one_row(self, client, session, auth_headers):
        first = (await _add(client, auth_headers, TEXT)).json()
        second = (await _add(client, auth_headers, TEXT + "\n")).json()
        assert second["created"] is False
        assert second["vacancy"]["id"] == first["vacancy"]["id"]
        assert await session.scalar(select(func.count()).select_from(Vacancy)) == 1

    async def test_a_text_is_never_shared_with_another_account(
        self, client, session, user, auth_headers
    ):
        """Two people pasting the same ad get two postings: each is one's own."""
        await _add(client, auth_headers, TEXT)
        posting = await session.scalar(select(Posting))
        assert posting.external_id.startswith(f"{user.id}:")
        # No dedup keys: nothing in the pool can be filed as a copy of it.
        assert posting.dedup_key is None and posting.text_fp is None

    async def test_an_unreadable_input_says_why(self, client, auth_headers):
        r = await _add(client, auth_headers, "Python")
        assert r.status_code == 422
        assert "короткий" in r.json()["detail"]

    async def test_the_free_filters_mark_it_but_do_not_refuse_it(
        self, client, session, user, auth_headers
    ):
        session.add(
            Resume(
                user_id=user.id,
                filename="cv.md",
                raw_text="текст",
                profile={"target_role": "Backend"},
                search_profile={"queries": ["python"], "negative": ["аналитик"]},
                is_active=True,
            )
        )
        await session.commit()
        body = (await _add(client, auth_headers, TEXT)).json()
        assert body["vacancy"]["stage"] == "screened_out"
        assert "аналитик" in body["vacancy"]["screen_reason"].lower()

    async def test_a_link_to_a_pooled_posting_reuses_it(
        self, client, session, user, auth_headers, monkeypatch
    ):
        """The scan's listing is richer than the page: it is not overwritten,
        and the user's row from the scan moves to «Мои вакансии»."""
        row = make_vacancy(
            user.id,
            source="habr",
            external_id="1000",
            url="https://career.habr.com/vacancies/1000",
            title="Python",
            qualification="Middle",
            stage="shortlist",
            hidden_at=None,
        )
        session.add(row)
        await session.commit()

        page = Listing(id="1000", url=row.posting.url, title="Python", company="Ромашка",
                       remote=None)
        monkeypatch.setattr(
            user_added, "from_url",
            lambda url: user_added.Added(source="habr", posting=page, text="Полный текст"),
        )
        body = (await _add(client, auth_headers, row.posting.url)).json()
        assert body["created"] is False
        assert body["vacancy"]["id"] == str(row.id)
        assert body["vacancy"]["qualification"] == "Middle"
        assert body["vacancy"]["added_at"] is not None
        await session.refresh(row.posting)
        assert row.posting.raw_text == "Полный текст"

    async def test_a_closed_page_is_added_marked(self, client, auth_headers, monkeypatch):
        page = Listing(id="77", url="https://hh.ru/vacancy/77", title="Бухгалтер", company="")
        monkeypatch.setattr(
            user_added, "from_url",
            lambda url: user_added.Added(
                source="hh", posting=page, text="текст", closed_reason="вакансия в архиве hh.ru"
            ),
        )
        await _add(client, auth_headers, page.url)
        [row] = await _found(client, auth_headers)
        assert row["closed"]["reason"] == "вакансия в архиве hh.ru"


@requires_db
class TestScanLeavesItAlone:
    async def test_clear_keeps_added_rows(self, client, auth_headers):
        await _add(client, auth_headers, TEXT)
        r = await client.post("/api/vacancies/clear", headers=auth_headers)
        assert r.json() == {"deleted": 0}
        assert len(await _found(client, auth_headers)) == 1

    async def test_triage_does_not_pay_for_added_rows(self, client, session, user, auth_headers):
        session.add(
            Resume(user_id=user.id, filename="cv.md", raw_text="т", profile={}, is_active=True)
        )
        await session.commit()
        await _add(client, auth_headers, TEXT)
        r = await client.post("/api/vacancies/triage", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["judged"] == 0


@requires_db
class TestRemove:
    async def test_removing_a_text_deletes_its_posting(self, client, session, auth_headers):
        vid = (await _add(client, auth_headers, TEXT)).json()["vacancy"]["id"]
        r = await client.delete(f"/api/vacancies/{vid}", headers=auth_headers)
        assert r.status_code == 204, r.text
        assert await session.scalar(select(func.count()).select_from(Posting)) == 0

    async def test_a_scanned_row_is_hidden_not_deleted(
        self, client, session, user, auth_headers
    ):
        row = make_vacancy(user.id, stage="shortlist")
        session.add(row)
        await session.commit()
        r = await client.delete(f"/api/vacancies/{row.id}", headers=auth_headers)
        assert r.status_code == 409

    async def test_deleting_the_account_takes_the_pasted_texts(
        self, client, session, auth_headers
    ):
        await _add(client, auth_headers, TEXT)
        r = await client.delete("/api/auth/me", headers=auth_headers)
        assert r.status_code == 204, r.text
        assert await session.scalar(select(func.count()).select_from(Posting)) == 0
