"""A vacancy the user brings to «Избранное» by its link.

The rules under test are the ones a rewrite would lose: only links to our
sources are read, and another site is never fetched; the page is read once,
at the preview; adding is free, saves the row and never duplicates one the
user has; nothing a scan does removes an added row.
"""

from __future__ import annotations

import socket
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import Posting, Resume, Vacancy
from core.sources import user_added
from core.sources.habr_api import Posting as Listing
from tests.dbmark import requires_db
from tests.pool_helpers import make_vacancy

LINK = "https://career.habr.com/vacancies/1000"


def _habr(monkeypatch, listing: Listing | None = None, text: str = "Полный текст") -> list[str]:
    """Serve one Habr page; returns the links that were read."""
    listing = listing or Listing(id="1000", url=LINK, title="Аналитик данных",
                                 company="Ромашка", remote=None, salary_text="от 200 000 ₽")
    read: list[str] = []

    def from_url(url: str) -> user_added.Added:
        read.append(url)
        return user_added.Added(source="habr", posting=listing, text=text)

    monkeypatch.setattr(user_added, "from_url", from_url)
    return read


async def _preview(client, headers, link: str):
    return await client.post("/api/vacancies/add/preview", json={"link": link}, headers=headers)


async def _add(client, headers, posting_id: str):
    return await client.post(
        "/api/vacancies/add", json={"posting_id": posting_id}, headers=headers
    )


async def _found(client, headers) -> list[dict]:
    return (await client.get("/api/state", headers=headers)).json()["found"]


@requires_db
class TestPreview:
    async def test_a_link_shows_what_it_is_without_making_a_row(
        self, client, session, auth_headers, monkeypatch
    ):
        _habr(monkeypatch)
        r = await _preview(client, auth_headers, LINK)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["title"] == "Аналитик данных"
        assert body["company"] == "Ромашка"
        assert body["saved"] is False
        assert await session.scalar(select(func.count()).select_from(Vacancy)) == 0

    async def test_text_is_refused(self, client, auth_headers):
        r = await _preview(client, auth_headers, "Аналитик данных, удалённо, от 200 000 ₽")
        assert r.status_code == 422
        assert r.json()["detail"] == user_added.NOT_ACCEPTED


@requires_db
class TestOtherSites:
    """A site we do not read is never fetched: an employer's link is found
    among pooled postings, anything else is refused."""

    @pytest.fixture(autouse=True)
    def no_network(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("a link to another site must not be resolved")

        monkeypatch.setattr(socket, "getaddrinfo", refuse)

    async def test_an_employer_link_a_scan_pooled_is_found(self, client, session, auth_headers):
        session.add(Posting(id=uuid.uuid4(), source="company", external_id="vk/vk/42",
                            url="https://team.vk.company/vacancy/42/", title="Аналитик",
                            company="VK"))
        await session.commit()

        r = await _preview(client, auth_headers, "https://team.vk.company/vacancy/42#apply")
        assert r.status_code == 200, r.text
        assert r.json()["company"] == "VK"

    @pytest.mark.parametrize(
        "link",
        ["https://jobs.example.com/a/1", "http://169.254.169.254/latest/meta-data/"],
    )
    async def test_an_unknown_site_is_refused(self, client, auth_headers, link):
        r = await _preview(client, auth_headers, link)
        assert r.status_code == 422
        assert r.json()["detail"] == user_added.NOT_ACCEPTED

    async def test_a_telegram_post_is_refused_even_when_pooled(
        self, client, session, auth_headers
    ):
        """A post can say anything: neither its link nor its id is accepted."""
        posting = Posting(id=uuid.uuid4(), source="telegram", external_id="jobs/1",
                          url="https://t.me/jobs/1", title="Вакансия")
        session.add(posting)
        await session.commit()
        assert (await _preview(client, auth_headers, posting.url)).status_code == 422
        assert (await _add(client, auth_headers, str(posting.id))).status_code == 404


@requires_db
class TestAdd:
    async def test_adding_saves_it_without_reading_the_page_again(
        self, client, auth_headers, monkeypatch
    ):
        read = _habr(monkeypatch)
        pid = (await _preview(client, auth_headers, LINK)).json()["posting_id"]
        r = await _add(client, auth_headers, pid)
        assert r.status_code == 200, r.text
        assert r.json()["created"] is True
        assert read == [LINK]

        [row] = await _found(client, auth_headers)
        assert row["saved"] is True
        assert row["added_at"] is not None

    async def test_a_row_the_user_has_is_saved_not_duplicated(
        self, client, session, user, auth_headers, monkeypatch
    ):
        """The scan's listing is richer than the page: it is not overwritten,
        and the user's hidden row from the scan comes back saved."""
        row = make_vacancy(
            user.id,
            source="habr",
            external_id="1000",
            url=LINK,
            title="Python",
            qualification="Middle",
            stage="shortlist",
            hidden_at=datetime.now(UTC),
        )
        session.add(row)
        await session.commit()
        _habr(monkeypatch)

        pid = (await _preview(client, auth_headers, LINK)).json()["posting_id"]
        body = (await _add(client, auth_headers, pid)).json()
        assert body["created"] is False
        assert body["vacancy"]["id"] == str(row.id)
        assert body["vacancy"]["qualification"] == "Middle"
        assert body["vacancy"]["is_saved"] is True
        assert body["vacancy"]["hidden_at"] is None
        assert await session.scalar(select(func.count()).select_from(Vacancy)) == 1
        assert (await _preview(client, auth_headers, LINK)).json()["saved"] is True

    async def test_the_free_filters_mark_it_but_do_not_refuse_it(
        self, client, session, user, auth_headers, monkeypatch
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
        _habr(monkeypatch)
        pid = (await _preview(client, auth_headers, LINK)).json()["posting_id"]
        body = (await _add(client, auth_headers, pid)).json()
        assert body["vacancy"]["stage"] == "screened_out"
        assert "аналитик" in body["vacancy"]["screen_reason"].lower()

    async def test_a_closed_page_is_added_marked(self, client, auth_headers, monkeypatch):
        page = Listing(id="77", url="https://hh.ru/vacancy/77", title="Бухгалтер", company="")
        monkeypatch.setattr(
            user_added, "from_url",
            lambda url: user_added.Added(
                source="hh", posting=page, text="текст", closed_reason="вакансия в архиве hh.ru"
            ),
        )
        preview = (await _preview(client, auth_headers, page.url)).json()
        assert preview["closed_reason"] == "вакансия в архиве hh.ru"
        await _add(client, auth_headers, preview["posting_id"])
        [row] = await _found(client, auth_headers)
        assert row["closed"]["reason"] == "вакансия в архиве hh.ru"

    async def test_an_unknown_posting_is_not_found(self, client, auth_headers):
        assert (await _add(client, auth_headers, str(uuid.uuid4()))).status_code == 404


@requires_db
class TestScanLeavesItAlone:
    async def test_clear_keeps_added_rows(self, client, auth_headers, monkeypatch):
        _habr(monkeypatch)
        pid = (await _preview(client, auth_headers, LINK)).json()["posting_id"]
        await _add(client, auth_headers, pid)
        r = await client.post("/api/vacancies/clear", headers=auth_headers)
        assert r.json() == {"deleted": 0}
        assert len(await _found(client, auth_headers)) == 1


async def _old_text(session, user) -> Vacancy:
    """A pasted text of before 2026-10-08: no new ones are made."""
    row = make_vacancy(user.id, source=user_added.TEXT_SOURCE,
                       external_id=f"{user.id}:abc", stage="shortlist", is_saved=True)
    row.added_at = datetime.now(UTC)
    session.add(row)
    await session.commit()
    return row


@requires_db
class TestRemove:
    async def test_removing_an_old_text_deletes_its_posting(
        self, client, session, user, auth_headers
    ):
        row = await _old_text(session, user)
        r = await client.delete(f"/api/vacancies/{row.id}", headers=auth_headers)
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

    async def test_deleting_the_account_takes_the_old_texts(
        self, client, session, user, auth_headers
    ):
        await _old_text(session, user)
        r = await client.delete("/api/auth/me", headers=auth_headers)
        assert r.status_code == 204, r.text
        assert await session.scalar(select(func.count()).select_from(Posting)) == 0
