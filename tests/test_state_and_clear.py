"""The screen's two supporting endpoints: /api/state and clearing the list.

`/api/state` exists because the cabinet was built against one aggregate call;
`clear` because "Очистить" is a button the demo already had, with a rule that
is easy to lose in a rewrite — it must never take the shortlist with it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.api.state import salary_band
from app.models import Evaluation, Resume, User, Vacancy
from app.models import Posting as PooledPosting
from app.services import scan
from core.sources.habr_api import Posting
from tests.dbmark import requires_db


def posting(pid: str, title: str = "Python разработчик") -> Posting:
    return Posting(
        id=pid,
        url=f"https://career.habr.com/vacancies/{pid}",
        title=title,
        company="ООО Ромашка",
        qualification="Middle",
    )


async def _resume_for(session, user: User) -> Resume:
    resume = Resume(
        user_id=user.id,
        filename="cv.md",
        raw_text="текст",
        profile={"target_role": "Backend"},
        is_active=True,
    )
    session.add(resume)
    await session.commit()
    await session.refresh(resume)
    return resume


@requires_db
class TestState:
    async def test_without_a_resume_it_says_so_rather_than_failing(
        self, client, auth_headers
    ):
        """The screen has to render something before a CV is uploaded."""
        r = await client.get("/api/state", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["has_resume"] is False
        assert body["found"] == []

    async def test_it_carries_the_vocabulary_the_screen_renders(
        self, client, auth_headers
    ):
        body = (await client.get("/api/state", headers=auth_headers)).json()
        assert "applied" in body["statuses"]
        assert {s["step"] for s in body["apply_steps"]} == {"tailor", "letter", "interview"}

    async def test_a_dropped_vacancy_stays_visible_with_its_reason(
        self, client, session, user, auth_headers
    ):
        """Rejection never goes silent — that rule outlives the storage change.

        The drop is not a row of the user's any more; the discard log
        recomputes it from the pool with the user's own filters.
        """
        resume = await _resume_for(session, user)
        resume.search_profile = {"queries": ["python"], "positive": ["Python"]}
        await scan.persist(
            session,
            user.id,
            "habr",
            kept=[(posting("1"), "")],
            dropped=[(posting("2", "Бариста"), "стоп-слово в заголовке: бариста")],
        )
        await session.commit()

        found = (await client.get("/api/state", headers=auth_headers)).json()["found"]
        assert [f["title"] for f in found] == ["Python разработчик"]

        r = await client.get("/api/vacancies/screened", headers=auth_headers)
        assert r.status_code == 200, r.text
        [dropped] = r.json()
        assert dropped["title"] == "Бариста"
        assert dropped["reason"]

    async def test_the_quota_travels_with_the_state_as_shares(
        self, client, auth_headers
    ):
        """Percentages and reset times — no money anywhere in the payload."""
        spend = (await client.get("/api/state", headers=auth_headers)).json()["spend"]
        assert spend["quota"]["plan"] == "free"
        assert spend["quota"]["day"]["used_pct"] == 0
        assert spend["quota"]["week"]["resets_at"]
        assert not any("rub" in key for key in spend)


@requires_db
class TestClear:
    async def test_it_drops_the_results(self, client, session, user, auth_headers):
        await _resume_for(session, user)
        await scan.persist(
            session, user.id, "habr", kept=[(posting("1"), ""), (posting("2"), "")], dropped=[]
        )
        await session.commit()

        r = await client.post("/api/vacancies/clear", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["deleted"] == 2

        left = (await session.execute(select(Vacancy))).scalars().all()
        assert left == []

    async def test_it_never_takes_the_shortlist(
        self, client, session, user, auth_headers
    ):
        """A shortlisted vacancy carries a report the user paid for.

        Losing it to a button labelled "очистить список найденного" would be
        data loss the label never warned about.
        """
        resume = await _resume_for(session, user)
        await scan.persist(
            session, user.id, "habr", kept=[(posting("1"), ""), (posting("2"), "")], dropped=[]
        )
        await session.commit()

        rows = (await session.execute(select(Vacancy).join(Vacancy.posting).order_by(PooledPosting.external_id))).scalars().all()
        keeper = rows[0]
        keeper.is_saved = True
        session.add(
            Evaluation(
                vacancy_id=keeper.id,
                resume_id=resume.id,
                score=4.5,
                verdict="apply",
                report={"score": 4.5},
                model="deepseek-flash",
                cost_rub=Decimal("0.17"),
            )
        )
        await session.commit()

        assert (await client.post("/api/vacancies/clear", headers=auth_headers)).json()[
            "deleted"
        ] == 1

        left = (await session.execute(select(Vacancy))).scalars().all()
        assert len(left) == 1 and left[0].id == keeper.id
        # The paid report survived with it.
        assert (await session.execute(select(Evaluation))).scalars().all()

    async def test_it_cannot_reach_another_account(
        self, client, session, user, auth_headers
    ):
        from app.core.security import hash_password

        other = User(
            email=f"o{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("x")
        )
        session.add(other)
        await session.flush()
        await scan.persist(session, other.id, "habr", kept=[(posting("9"), "")], dropped=[])
        await session.commit()

        await client.post("/api/vacancies/clear", headers=auth_headers)

        theirs = (
            await session.execute(select(Vacancy).where(Vacancy.user_id == other.id))
        ).scalars().all()
        assert len(theirs) == 1


class TestSalaryBand:
    """The cabinet filters found rows by salary; it needs the band as numbers."""

    @staticmethod
    def band(salary_from=None, salary_to=None, currency=None):
        return salary_band(PooledPosting(
            salary_from=salary_from, salary_to=salary_to, currency=currency
        ))

    def test_reads_a_rouble_band(self):
        assert self.band(150_000, 250_000, "RUB") == (150_000, 250_000)

    def test_habr_names_the_rouble_rur(self):
        assert self.band(150_000, None, "rur") == (150_000, None)

    def test_an_open_lower_end_is_none(self):
        assert self.band(None, 300_000, "RUB") == (None, 300_000)

    def test_another_currency_counts_as_not_stated(self):
        # Compared against a rouble threshold it would cut or pass at random.
        assert self.band(3000, None, "usd") == (None, None)

    def test_no_numbers_is_not_stated(self):
        assert self.band() == (None, None)
