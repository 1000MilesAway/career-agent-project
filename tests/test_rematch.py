"""A changed search profile rematches the found list from the pool.

Free and offline: no fetch function is reachable from `scan.rematch`, so the
tests replace none — a network call would fail them, not pass silently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import Application, Evaluation, Resume, User, Vacancy
from app.models import Posting as Pooled
from app.services import scan
from tests.dbmark import requires_db
from tests.pool_helpers import make_vacancy

PYTHON = {"queries": ["python"], "positive": ["python"]}
GO = {"queries": ["go"], "positive": ["go"]}


async def _user(session) -> User:
    user = User(email=f"u{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def _titles(session, user_id) -> list[str]:
    rows = await session.execute(select(Vacancy).where(Vacancy.user_id == user_id))
    return sorted(v.title for v in rows.scalars())


@requires_db
class TestRematch:
    async def test_untouched_rows_the_new_profile_drops_are_removed(self, session):
        user = await _user(session)
        session.add(make_vacancy(user.id, title="Python developer", stage="shortlist"))
        await session.flush()

        result = await scan.rematch(session, user.id, GO, sources=("habr",))

        assert result["removed"] == 1
        assert result["reasons"] == [
            {"reason": "заголовок не совпал ни с одним ключевым словом", "count": 1}
        ]
        assert await _titles(session, user.id) == []
        # The posting stays in the pool for everyone else.
        assert (await session.execute(select(Pooled))).scalars().all()

    async def test_pooled_postings_the_new_profile_keeps_are_added(self, session):
        user = await _user(session)
        session.add(Pooled(id=uuid.uuid4(), source="habr", external_id="g1", title="Go developer"))
        await session.flush()

        result = await scan.rematch(session, user.id, GO, sources=("habr",))

        assert result["added"] == 1
        assert await _titles(session, user.id) == ["Go developer"]

    async def test_rows_the_user_acted_on_stay(self, session):
        user = await _user(session)
        resume = Resume(user_id=user.id, filename="cv", raw_text="", profile={}, is_active=True)
        session.add(resume)
        saved = make_vacancy(user.id, title="Python saved", stage="shortlist", is_saved=True)
        hidden = make_vacancy(
            user.id, title="Python hidden", stage="shortlist", hidden_at=datetime.now(UTC)
        )
        triaged = make_vacancy(
            user.id, title="Python triaged", stage="shortlist", triage_verdict="maybe"
        )
        evaluated = make_vacancy(user.id, title="Python evaluated", stage="shortlist")
        tracked = make_vacancy(user.id, title="Python tracked", stage="shortlist")
        session.add_all([saved, hidden, triaged, evaluated, tracked])
        await session.flush()
        session.add(Evaluation(vacancy_id=evaluated.id, resume_id=resume.id, report={}, model="m"))
        session.add(Application(vacancy_id=tracked.id))
        await session.flush()

        result = await scan.rematch(session, user.id, GO, sources=("habr",))

        assert result["removed"] == 0
        assert len(await _titles(session, user.id)) == 5

    async def test_form_owned_conditions_do_not_drop_rows(self, session):
        """The scan form sends salary, remote, accreditation and city every
        time; the stored values never decided a row and must not now."""
        user = await _user(session)
        session.add(make_vacancy(
            user.id, title="Python developer", stage="shortlist",
            remote=False, location="Казань", accredited=False,
            salary_from=50_000, salary_to=60_000, currency="RUR", salary_text="50–60 тыс.",
        ))
        await session.flush()
        strict = {
            **PYTHON, "remote_only": True, "accredited_only": True,
            "cities": ["Москва"], "salary_min": 300_000,
        }

        result = await scan.rematch(session, user.id, strict, sources=("habr",))

        assert result["removed"] == 0
        assert await _titles(session, user.id) == ["Python developer"]


@requires_db
class TestAcceptSearchProfile:
    async def _resume(self, session, user, search_profile) -> None:
        session.add(Resume(
            user_id=user.id, filename="cv", raw_text="", profile={},
            search_profile=search_profile, is_active=True,
        ))
        session.add(make_vacancy(user.id, title="Python developer", stage="shortlist"))
        await session.commit()

    async def test_a_changed_profile_reports_the_rematch(
        self, client, auth_headers, session, user
    ):
        await self._resume(session, user, PYTHON)

        r = await client.put("/api/resumes/search-profile", headers=auth_headers, json=GO)

        assert r.status_code == 200, r.text
        assert r.json()["search_profile"] == GO
        assert r.json()["rematch"]["removed"] == 1

    async def test_the_same_profile_touches_nothing(self, client, auth_headers, session, user):
        await self._resume(session, user, GO)

        r = await client.put("/api/resumes/search-profile", headers=auth_headers, json=GO)

        assert r.status_code == 200, r.text
        assert r.json()["rematch"] is None
        assert await _titles(session, user.id) == ["Python developer"]
