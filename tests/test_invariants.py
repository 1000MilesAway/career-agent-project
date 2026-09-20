"""The rules from CLAUDE.md, checked against the database implementation.

These are the expensive lessons from the demo. A rewrite onto Postgres is
exactly where they get quietly dropped, so each one has a test that fails
loudly if it does.

No LLM is called anywhere here.
"""

from __future__ import annotations

import uuid

import pytest

from app.models import Application, Artifact, Evaluation, Resume, User, Vacancy
from app.services import scan
from app.services.tracker import set_status
from core.sources.habr_api import Posting
from tests.dbmark import requires_db


def posting(pid: str = "1", title: str = "Python разработчик", **kw) -> Posting:
    base = {
        "id": pid,
        "url": f"https://career.habr.com/vacancies/{pid}",
        "title": title,
        "company": "ООО Ромашка",
        "qualification": "Middle",
        "salary_text": "200 000 ₽",
        "divisions": ["backend"],
        "skills": ["Python"],
    }
    return Posting(**{**base, **kw})


async def _user_with_resume(session) -> tuple[User, Resume]:
    from app.core.security import hash_password

    user = User(
        email=f"u{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("x"),
    )
    session.add(user)
    await session.flush()

    resume = Resume(
        user_id=user.id,
        filename="cv.md",
        raw_text="текст резюме",
        profile={"target_role": "Backend"},
        is_active=True,
    )
    session.add(resume)
    await session.flush()
    return user, resume


@requires_db
class TestRejectionIsNeverSilent:
    """CLAUDE.md: «Отсев никогда не молчит»."""

    async def test_a_dropped_posting_is_stored_with_its_reason(self, session):
        user, _ = await _user_with_resume(session)

        await scan.persist(
            session,
            user.id,
            "habr",
            kept=[(posting("1"), "")],
            dropped=[(posting("2", title="Бариста"), "стоп-слово в заголовке: бариста")],
        )
        await session.commit()

        from sqlalchemy import select

        rows = (await session.execute(select(Vacancy).order_by(Vacancy.external_id))).scalars()
        by_id = {v.external_id: v for v in rows}

        assert by_id["2"].stage == scan.SCREENED_OUT
        assert "бариста" in by_id["2"].screen_reason
        # Still there: hidden-but-visible, not deleted.
        assert by_id["2"].title == "Бариста"

    async def test_a_kept_posting_carries_no_reason(self, session):
        user, _ = await _user_with_resume(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        from sqlalchemy import select

        row = (await session.execute(select(Vacancy))).scalar_one()
        assert row.stage == scan.SHORTLIST
        assert row.screen_reason is None


@requires_db
class TestAScanNeedsAnAcceptedProfile:
    """CLAUDE.md: «Ни один платный вызов не делается до бесплатных ступеней».

    An empty profile states no stop-word and no keyword, so PreScreen keeps
    everything: a real run on 2026-09-19 stored 125 rows — SMM, legal, product
    management — none carrying a rejection reason. Free-stage damage, but it is
    the shortlist a paid triage runs on next.
    """

    async def test_a_scan_without_a_profile_is_refused(self, session):
        user, _ = await _user_with_resume(session)

        with pytest.raises(ValueError, match="профиль поиска не принят"):
            await scan.collect_habr(session, user.id, None)

    async def test_an_empty_profile_is_refused_too(self, session):
        """`{}` reaches PreScreen as "keep everything", same as None."""
        user, _ = await _user_with_resume(session)

        with pytest.raises(ValueError, match="профиль поиска не принят"):
            await scan.collect_habr(session, user.id, {})

    async def test_a_profile_without_queries_is_refused(self, session):
        """An empty query asks Habr for the whole catalogue — the same flood."""
        user, _ = await _user_with_resume(session)

        with pytest.raises(ValueError, match="нет ни одного запроса"):
            await scan.collect_habr(session, user.id, {"positive": ["Python"]})


@requires_db
class TestARescanNeverDestroysWhatWasPaidFor:
    """The demo's merge rule: refresh the listing, never the judgement."""

    async def test_a_second_scan_refreshes_salary_but_keeps_the_evaluation(self, session):
        user, resume = await _user_with_resume(session)

        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        from sqlalchemy import select

        vacancy = (await session.execute(select(Vacancy))).scalar_one()
        session.add(
            Evaluation(
                vacancy_id=vacancy.id,
                resume_id=resume.id,
                score=4.5,
                verdict="apply",
                report={"score": 4.5},
                model="deepseek-flash",
                cost_rub=0.17,
            )
        )
        vacancy.stage = scan.EVALUATED
        await session.commit()

        # The posting reappears in a later scan with a new salary.
        await scan.persist(
            session,
            user.id,
            "habr",
            kept=[(posting("1", salary_text="250 000 ₽"), "")],
            dropped=[],
        )
        await session.commit()

        refreshed = (await session.execute(select(Vacancy))).scalar_one()
        evaluation = (await session.execute(select(Evaluation))).scalar_one()

        assert refreshed.salary_text == "250 000 ₽"  # listing updated
        assert evaluation.score == 4.5  # judgement untouched
        assert refreshed.stage == scan.EVALUATED  # progress not rewound

    async def test_a_rescan_does_not_overwrite_the_stored_text(self, session):
        """For Telegram this is the only copy of the posting.

        Overwriting it would silently re-point a stored report at a posting it
        never judged.
        """
        user, _ = await _user_with_resume(session)

        await scan.persist(
            session,
            user.id,
            "telegram",
            kept=[(posting("10"), "")],
            dropped=[],
            texts={"10": "исходный текст"},
        )
        await session.commit()

        await scan.persist(
            session,
            user.id,
            "telegram",
            kept=[(posting("10"), "")],
            dropped=[],
            texts={"10": "другой текст"},
        )
        await session.commit()

        from sqlalchemy import select

        row = (await session.execute(select(Vacancy))).scalar_one()
        assert row.raw_text == "исходный текст"

    async def test_the_same_posting_twice_is_one_row(self, session):
        user, _ = await _user_with_resume(session)
        for _ in range(2):
            await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
            await session.commit()

        from sqlalchemy import func, select

        count = (await session.execute(select(func.count()).select_from(Vacancy))).scalar_one()
        assert count == 1


@requires_db
class TestTracker:
    async def test_transitions_are_appended_not_overwritten(self, session):
        user, _ = await _user_with_resume(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        vacancy = (
            await session.execute(select(Vacancy).options(selectinload(Vacancy.application)))
        ).scalar_one()

        set_status(vacancy, "applied")
        await session.commit()
        set_status(vacancy, "responded")
        await session.commit()
        set_status(vacancy, "interview", note="во вторник")
        await session.commit()

        application = (await session.execute(select(Application))).scalar_one()
        assert application.status == "interview"
        assert [e["to"] for e in application.status_log] == ["applied", "responded", "interview"]
        # The log remembers where it came from, which the current state cannot.
        assert application.status_log[1]["from"] == "applied"
        assert application.status_log[2]["note"] == "во вторник"

    async def test_an_unknown_status_is_refused_before_anything_is_written(self, session):
        user, _ = await _user_with_resume(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        vacancy = (
            await session.execute(select(Vacancy).options(selectinload(Vacancy.application)))
        ).scalar_one()

        with pytest.raises(ValueError):
            set_status(vacancy, "нет такого")
        assert vacancy.application is None


@requires_db
class TestPaidWorkIsNotBoughtTwice:
    async def test_one_evaluation_per_vacancy_and_resume(self, session):
        """The unique constraint is what makes 'cached' safe to rely on."""
        user, resume = await _user_with_resume(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        from sqlalchemy import select

        vacancy = (await session.execute(select(Vacancy))).scalar_one()

        for _ in range(2):
            session.add(
                Evaluation(
                    vacancy_id=vacancy.id,
                    resume_id=resume.id,
                    report={},
                    model="deepseek-flash",
                )
            )

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await session.commit()

    async def test_one_artifact_of_each_kind_per_evaluation(self, session):
        user, resume = await _user_with_resume(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        from sqlalchemy import select

        vacancy = (await session.execute(select(Vacancy))).scalar_one()
        evaluation = Evaluation(
            vacancy_id=vacancy.id, resume_id=resume.id, report={}, model="deepseek-flash"
        )
        session.add(evaluation)
        await session.flush()

        session.add(Artifact(evaluation_id=evaluation.id, kind="letter", content={"text": "a"}))
        session.add(Artifact(evaluation_id=evaluation.id, kind="letter", content={"text": "b"}))

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await session.commit()


@requires_db
class TestOneActiveResume:
    async def test_two_active_resumes_are_impossible(self, session):
        """The pipeline must never have to guess which profile to score against."""
        user, _ = await _user_with_resume(session)
        session.add(
            Resume(
                user_id=user.id,
                filename="second.md",
                raw_text="другое",
                profile={},
                is_active=True,
            )
        )

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await session.commit()
