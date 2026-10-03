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
from app.models import Posting as PooledPosting
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

    async def test_a_dropped_posting_keeps_its_reason(self, session):
        """No row per user for a drop — the reason is recomputed from the pool."""
        user, _ = await _user_with_resume(session)

        await scan.persist(
            session,
            user.id,
            "habr",
            kept=[(posting("1"), "")],
            dropped=[(posting("2", title="Бариста"), "стоп-слово в заголовке: бариста")],
        )
        await session.commit()

        profile = {"queries": ["python"], "positive": ["Python", "Бариста"],
                   "negative": ["бариста"]}
        screened = await scan.explain_screened(session, user.id, profile)

        by_title = {row["title"]: row for row in screened}
        assert "бариста" in by_title["Бариста"]["reason"]
        # The kept one is the user's row, not in the discard log.
        assert "Python разработчик" not in by_title

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

    async def test_uploading_a_new_cv_keeps_the_reports_bought_against_the_old_one(
        self, session
    ):
        """Replacing a CV must not take paid-for evaluations with it.

        `POST /api/resumes` deactivates the previous CV instead of deleting it,
        and the reason is a foreign key: `evaluations.resume_id` is declared
        ON DELETE CASCADE, so a DELETE would silently take every report and
        artifact bought against that CV. Uploading a newer CV is the ordinary
        way a user replaces one, and it must stay free of that cost.
        """
        from sqlalchemy import select, update

        user, old_resume = await _user_with_resume(session)

        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        vacancy = (await session.execute(select(Vacancy))).scalar_one()
        session.add(
            Evaluation(
                vacancy_id=vacancy.id,
                resume_id=old_resume.id,
                score=4.0,
                verdict="apply",
                report={"score": 4.0},
                model="deepseek-flash",
                cost_rub=0.17,
            )
        )
        await session.commit()

        # What the upload endpoint does: deactivate, then add. Never delete.
        await session.execute(
            update(Resume)
            .where(Resume.user_id == user.id, Resume.is_active.is_(True))
            .values(is_active=False)
        )
        session.add(
            Resume(
                user_id=user.id,
                filename="newer.pdf",
                raw_text="более свежее резюме",
                profile={"target_role": "Backend"},
                is_active=True,
            )
        )
        await session.commit()

        evaluations = list((await session.execute(select(Evaluation))).scalars())
        assert len(evaluations) == 1
        assert evaluations[0].score == 4.0
        assert evaluations[0].resume_id == old_resume.id

        # The old CV is still there to say what that report was judged against.
        stored = list((await session.execute(select(Resume))).scalars())
        assert len(stored) == 2
        assert sum(1 for r in stored if r.is_active) == 1


@requires_db
class TestPaidVerdictsSurviveAFreeRescan:
    """A free re-screen may revise its own verdict, never a paid one."""

    async def test_a_rescan_does_not_resurrect_a_triaged_out_vacancy(self, session):
        user, _ = await _user_with_resume(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        from sqlalchemy import select

        row = (await session.execute(select(Vacancy))).scalar_one()
        row.stage = scan.TRIAGED_OUT
        row.triage_verdict = "skip"
        row.screen_reason = "чужой стек"
        await session.commit()

        # The posting is still in the catalogue and still passes the free
        # filters, so a second scan sees it again.
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        await session.refresh(row)
        assert row.stage == scan.TRIAGED_OUT
        # The reason must not be wiped: a "мимо" tag with no reason under it
        # is the silent rejection the pipeline forbids.
        assert row.screen_reason == "чужой стек"
        assert row.triage_verdict == "skip"

    async def test_a_screened_out_vacancy_is_still_re_judged(self, session):
        """The free filters own their own verdict and may change their mind.

        A dropped posting has no row; a row the user acted on (saved) that the
        filters once dropped comes back to the shortlist when they keep it.
        """
        user, _ = await _user_with_resume(session)
        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        from sqlalchemy import select

        row = (await session.execute(select(Vacancy))).scalar_one()
        row.stage = scan.SCREENED_OUT
        row.screen_reason = "стоп-слово"
        row.is_saved = True
        await session.commit()

        await scan.persist(session, user.id, "habr", kept=[(posting("1"), "")], dropped=[])
        await session.commit()

        await session.refresh(row)
        assert row.stage == scan.SHORTLIST
        assert row.screen_reason is None


@requires_db
class TestPaidWorkIsNotRepeated:
    """Evaluations and artifacts refuse to be bought twice; so must triage."""

    async def test_a_second_triage_skips_what_already_has_a_verdict(
        self, client, session, user, auth_headers, monkeypatch
    ):
        from app.models import Resume

        resume = Resume(
            user_id=user.id, filename="cv.md", raw_text="x",
            profile={"skills": ["Python"]}, archetypes={"roles": []},
            search_profile={"queries": ["Python"]}, is_active=True,
        )
        session.add(resume)
        await scan.persist(
            session, user.id, "habr",
            kept=[(posting("1"), ""), (posting("2"), "")], dropped=[],
        )
        await session.commit()

        from sqlalchemy import select

        rows = list((await session.execute(
            select(Vacancy).join(Vacancy.posting).order_by(PooledPosting.external_id)
        )).scalars())
        rows[0].triage_verdict = "worth"
        rows[0].screen_reason = "подходит"
        await session.commit()

        judged_ids = []

        def fake_triage(llm, archetypes, digests):
            judged_ids.extend(d["id"] for d in digests)

            class Outcome:
                verdicts = {}
                missing: list = []
                unknown: list = []

                def counts(self):
                    return {}

            return Outcome()

        from app.services import triage as triage_service

        class StubLLM:
            """The endpoint builds a client before calling the stubbed step.

            Stubbing run_triage alone still hit the real constructor, which
            demands DEEPSEEK_API_KEY — so this passed only next to a .env and
            errored in CI, where the key is deliberately absent.
            """

            def __init__(self, *a, **kw) -> None:
                self.ledger = kw.get("ledger")

        monkeypatch.setattr(triage_service, "LLM", StubLLM)
        monkeypatch.setattr(triage_service, "run_triage", fake_triage)

        r = await client.post("/api/vacancies/triage", headers=auth_headers)
        assert r.status_code == 200, r.text

        # Only the unjudged one was sent to the model.
        assert judged_ids == [str(rows[1].id)]


    async def test_triage_judges_only_the_rows_the_conditions_keep(
        self, client, session, user, auth_headers, monkeypatch
    ):
        """A row the cabinet's conditions fold away is not sent to the model."""
        from sqlalchemy import select

        from app.models import Resume
        from app.services import triage as triage_service

        session.add(
            Resume(
                user_id=user.id, filename="cv.md", raw_text="x",
                profile={"skills": ["Python"]}, archetypes={"roles": []},
                search_profile={"queries": ["Python"]}, is_active=True,
            )
        )
        await scan.persist(
            session, user.id, "habr",
            kept=[(posting("1"), ""), (posting("2"), "")], dropped=[],
        )
        await session.commit()
        rows = list((await session.execute(
            select(Vacancy).join(Vacancy.posting).order_by(PooledPosting.external_id)
        )).scalars())

        judged_ids = []

        class Outcome:
            verdicts: dict = {}
            missing: list = []
            unknown: list = []

            def counts(self):
                return {}

        def fake_triage(llm, archetypes, digests):
            judged_ids.extend(d["id"] for d in digests)
            return Outcome()

        class StubLLM:
            def __init__(self, *a, **kw) -> None:
                self.ledger = kw.get("ledger")

        monkeypatch.setattr(triage_service, "LLM", StubLLM)
        monkeypatch.setattr(triage_service, "run_triage", fake_triage)

        r = await client.post(
            "/api/vacancies/triage", headers=auth_headers, json={"ids": [str(rows[1].id)]}
        )
        assert r.status_code == 200, r.text
        assert judged_ids == [str(rows[1].id)]


class TestAPaidFailureIsNotRetried:
    """The rollback after a failed job undoes `charge` too.

    Retrying then buys the same tokens again and records none of it, so a
    failure that happens AFTER the model ran must be terminal. `tick` decides
    that from the exception type; the decision itself is what matters here,
    and it is checked without a worker session of its own.
    """

    @staticmethod
    def _next_status(error: Exception, attempts: int) -> str:
        """The branch `tick` takes, spelled out — see app/worker.py."""
        from app.worker import MAX_ATTEMPTS, PaidFailure

        retryable = not isinstance(error, PaidFailure)
        return "queued" if retryable and attempts < MAX_ATTEMPTS else "failed"

    def test_a_paid_failure_fails_at_once(self):
        from app.worker import PaidFailure

        assert self._next_status(PaidFailure("пустой результат"), attempts=1) == "failed"

    def test_an_ordinary_failure_is_still_retried(self):
        """Nothing was bought, so a second attempt costs nothing to try."""
        assert self._next_status(ValueError("сеть недоступна"), attempts=1) == "queued"

    def test_retries_still_run_out(self):
        assert self._next_status(ValueError("сеть недоступна"), attempts=3) == "failed"

    def test_the_artifact_builder_raises_the_terminal_kind(self):
        """The empty-result branch is the one that already spent the money."""
        import inspect

        from app import worker

        body = inspect.getsource(worker._run_artifact)
        after_check = body.split("if not content:")[1]
        assert "PaidFailure" in after_check[:300]
