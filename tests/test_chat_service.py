"""The chat's tools against a real database.

What is pinned here is the property the whole feature rests on: a proposing
tool prepares a row and changes nothing. If that ever stops being true, the
confirmation layer becomes decoration.

No LLM is called anywhere in this file.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import Evaluation, Proposal, Resume, User, Vacancy
from app.services import chat as chat_service
from app.services import revisions
from app.services.tracker import set_status
from core import tools
from tests.dbmark import requires_db


async def _user(session) -> User:
    from app.core.security import hash_password

    account = User(
        email=f"u{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("x"),
    )
    session.add(account)
    await session.flush()
    return account


async def _resume(session, user: User, profile: dict | None = None) -> Resume:
    resume = Resume(
        user_id=user.id,
        filename="cv.pdf",
        raw_text="текст резюме",
        profile=profile or {"target_role": "Backend", "skills": ["Python"]},
        is_active=True,
    )
    session.add(resume)
    await session.flush()
    return resume


async def _vacancy(session, user: User, title: str = "Python разработчик") -> Vacancy:
    vacancy = Vacancy(
        user_id=user.id,
        source="habr",
        external_id=uuid.uuid4().hex[:8],
        title=title,
        company="ООО Ромашка",
        raw_text="Требуется Python и PostgreSQL",
        stage="shortlist",
    )
    session.add(vacancy)
    await session.flush()
    return vacancy


class TestSplitRequirements:
    """Three buckets, as in career-ops' jd-skill-gap. No model, no database."""

    def test_requirements_are_bucketed_by_match(self):
        report = {
            "requirements": [
                {"text": "Python", "match": "full", "evidence": "5 лет"},
                {"text": "Kubernetes", "match": "partial", "evidence": "трогал"},
                {"text": "Rust", "match": "none", "gap_advice": "не закрыть быстро"},
            ]
        }
        out = chat_service.split_requirements(report)
        assert [r["text"] for r in out["covered"]] == ["Python"]
        assert [r["text"] for r in out["partial"]] == ["Kubernetes"]
        assert [r["text"] for r in out["missing"]] == ["Rust"]

    def test_an_empty_report_is_not_an_error(self):
        assert chat_service.split_requirements(None) == {
            "covered": [],
            "partial": [],
            "missing": [],
        }

    def test_an_unknown_match_counts_as_missing(self):
        """Cautious: an unrecognised value must not be read as "covered"."""
        out = chat_service.split_requirements({"requirements": [{"text": "X", "match": "?"}]})
        assert [r["text"] for r in out["missing"]] == ["X"]


class TestEnabledTools:
    def test_without_a_cv_the_profile_tools_are_withheld(self):
        """Offering them anyway produces a confident call that fails, and the
        model apologises instead of saying "upload a CV first"."""
        names = {t.name for t in chat_service.enabled_tools(has_resume=False)}
        assert "get_profile" not in names
        assert "propose_profile_patch" not in names
        assert "list_vacancies" in names

    def test_with_a_cv_everything_is_offered(self):
        assert chat_service.enabled_tools(has_resume=True) == tools.TOOLS


class TestApplyProfilePatch:
    """Accepted patches are additive and carry their provenance."""

    def test_a_skill_is_appended(self):
        out = chat_service.apply_profile_patch(
            {"skills": ["Python"]}, {"op": "add_skill", "value": "Kubernetes"}
        )
        assert out["skills"] == ["Python", "Kubernetes"]

    def test_a_duplicate_skill_is_not_added_twice(self):
        out = chat_service.apply_profile_patch(
            {"skills": ["Python"]}, {"op": "add_skill", "value": "python"}
        )
        assert out["skills"] == ["Python"]

    def test_the_source_of_every_addition_is_recorded(self):
        """A tailoring step has to tell a fact off the CV from a sentence
        typed into a chat."""
        out = chat_service.apply_profile_patch(
            {"skills": []}, {"op": "add_skill", "value": "Kubernetes"}
        )
        assert out["provenance"]["skills"][0]["source"] == "user-stated"

    def test_an_achievement_lands_in_the_named_company(self):
        profile = {"experience": [{"company": "Авито", "achievements": ["Платежи"]}]}
        out = chat_service.apply_profile_patch(
            profile,
            {"op": "add_achievement", "value": "Кластеры Kubernetes", "company": "Авито"},
        )
        assert out["experience"][0]["achievements"] == ["Платежи", "Кластеры Kubernetes"]

    @pytest.mark.parametrize(
        "stored,said",
        [
            # The case that shipped: the chat named the project without the note.
            ("Berezka VPN (свой проект, self-hosted)", "Berezka VPN"),
            ("iiko, Компания Айко", "iiko"),
            ("Лемана ПРО", "лемана про"),
        ],
    )
    def test_an_achievement_finds_the_company_by_its_short_name(self, stored, said):
        profile = {"experience": [{"company": "Авито"}, {"company": stored, "achievements": []}]}
        out = chat_service.apply_profile_patch(
            profile, {"op": "add_achievement", "value": "Claude Code", "company": said}
        )
        assert len(out["experience"]) == 2
        assert out["experience"][1]["achievements"] == ["Claude Code"]

    def test_an_ambiguous_short_name_does_not_guess(self):
        """Two employers both start with the name: picking one would put the
        achievement under the wrong job without saying so."""
        profile = {"experience": [{"company": "Сбер Технологии"}, {"company": "Сбер Маркет"}]}
        out = chat_service.apply_profile_patch(
            profile, {"op": "add_achievement", "value": "Что-то", "company": "Сбер"}
        )
        assert [e["company"] for e in out["experience"]] == [
            "Сбер Технологии", "Сбер Маркет", "Сбер",
        ]

    def test_an_unmatched_company_is_kept_visibly_not_dropped(self):
        """Silently discarding an accepted change is worse than placing it
        somewhere the user can see and fix."""
        out = chat_service.apply_profile_patch(
            {"experience": [{"company": "Авито"}]},
            {"op": "add_achievement", "value": "Что-то", "company": "Яндекс"},
        )
        companies = [e["company"] for e in out["experience"]]
        assert companies == ["Авито", "Яндекс"]

    def test_nothing_is_removed_by_a_patch(self):
        """Additive only, as in career-ops' expand mode."""
        profile = {
            "skills": ["Python"],
            "experience": [{"company": "Авито", "achievements": ["Платежи"]}],
            "education": ["МГУ"],
        }
        out = chat_service.apply_profile_patch(
            profile, {"op": "add_skill", "value": "Go"}
        )
        assert out["education"] == ["МГУ"]
        assert out["experience"] == profile["experience"]

    def test_set_field_refuses_structural_fields(self):
        """Letting a chat replace `experience` wholesale would make
        "additive only" meaningless."""
        profile = {"experience": [{"company": "Авито"}]}
        out = chat_service.apply_profile_patch(
            profile, {"op": "set_field", "field": "experience", "value": "ничего"}
        )
        assert out["experience"] == [{"company": "Авито"}]

    def test_set_field_accepts_a_simple_field(self):
        out = chat_service.apply_profile_patch(
            {}, {"op": "set_field", "field": "target_role", "value": "SRE"}
        )
        assert out["target_role"] == "SRE"

    def test_the_original_profile_is_not_mutated(self):
        profile = {"skills": ["Python"]}
        chat_service.apply_profile_patch(profile, {"op": "add_skill", "value": "Go"})
        assert profile["skills"] == ["Python"]


@requires_db
class TestReadTools:
    async def test_the_profile_is_returned(self, session):
        user = await _user(session)
        await _resume(session, user)
        run = chat_service.ToolRunner(session, user)
        out = await run("get_profile", {})
        assert out["profile"]["target_role"] == "Backend"

    async def test_without_a_cv_the_profile_tool_says_so(self, session):
        user = await _user(session)
        run = chat_service.ToolRunner(session, user)
        assert "error" in await run("get_profile", {})

    async def test_another_users_vacancy_is_invisible(self, session):
        """Ownership is checked in the query, not after: the chat must not be
        a way to read someone else's board."""
        owner = await _user(session)
        intruder = await _user(session)
        vacancy = await _vacancy(session, owner)
        await session.commit()

        run = chat_service.ToolRunner(session, intruder)
        out = await run("get_vacancy", {"vacancy_id": str(vacancy.id)})
        assert out == {"error": "вакансия не найдена"}

    async def test_a_malformed_id_is_an_error_not_a_crash(self, session):
        user = await _user(session)
        await session.commit()
        run = chat_service.ToolRunner(session, user)
        assert "error" in await run("get_vacancy", {"vacancy_id": "не-uuid"})

    async def test_skill_gap_needs_an_evaluation_against_the_current_cv(self, session):
        user = await _user(session)
        await _resume(session, user)
        vacancy = await _vacancy(session, user)
        await session.commit()

        run = chat_service.ToolRunner(session, user)
        out = await run("skill_gap", {"vacancy_id": str(vacancy.id)})
        assert "error" in out

    async def test_skill_gap_reads_a_bought_report(self, session):
        user = await _user(session)
        resume = await _resume(session, user)
        vacancy = await _vacancy(session, user)
        session.add(
            Evaluation(
                vacancy_id=vacancy.id,
                resume_id=resume.id,
                score=3.5,
                verdict="apply_with_caveats",
                report={
                    "score": 3.5,
                    "requirements": [
                        {"text": "Python", "match": "full"},
                        {"text": "Kubernetes", "match": "none"},
                    ],
                },
                model="deepseek-flash",
                cost_rub=0.17,
            )
        )
        await session.commit()

        run = chat_service.ToolRunner(session, user)
        out = await run("skill_gap", {"vacancy_id": str(vacancy.id)})
        assert [r["text"] for r in out["missing"]] == ["Kubernetes"]
        assert out["score"] == 3.5

    async def test_a_report_bought_against_an_older_cv_is_not_shown_as_current(
        self, session
    ):
        """state.py falls back to any evaluation and quietly shows a score
        computed against a profile that no longer exists. Not here."""
        user = await _user(session)
        old = await _resume(session, user)
        vacancy = await _vacancy(session, user)
        session.add(
            Evaluation(
                vacancy_id=vacancy.id,
                resume_id=old.id,
                score=4.0,
                verdict="apply",
                report={"score": 4.0, "requirements": []},
                model="deepseek-flash",
                cost_rub=0.17,
            )
        )
        old.is_active = False
        await _resume(session, user, {"target_role": "SRE"})
        await session.commit()

        run = chat_service.ToolRunner(session, user)
        out = await run("get_vacancy", {"vacancy_id": str(vacancy.id)})
        assert "report" not in out


@requires_db
class TestProposalsNeverWrite:
    """The property the confirmation layer rests on."""

    async def test_a_profile_patch_writes_nothing_to_the_resume(self, session):
        user = await _user(session)
        resume = await _resume(session, user)
        await session.commit()

        run = chat_service.ToolRunner(session, user)
        out = await run(
            "propose_profile_patch", {"op": "add_skill", "value": "Kubernetes"}
        )
        await session.commit()

        assert out["status"] == "pending_confirmation"
        await session.refresh(resume)
        assert resume.profile["skills"] == ["Python"]

    async def test_a_proposal_row_is_recorded_as_pending(self, session):
        user = await _user(session)
        await _resume(session, user)
        await session.commit()

        run = chat_service.ToolRunner(session, user)
        await run("propose_profile_patch", {"op": "add_skill", "value": "Go"})
        await session.commit()

        stored = list((await session.execute(select(Proposal))).scalars())
        assert len(stored) == 1
        assert stored[0].status == chat_service.PENDING
        assert stored[0].kind == "profile_patch"

    async def test_a_status_change_does_not_move_the_tracker(self, session):
        user = await _user(session)
        vacancy = await _vacancy(session, user)
        await session.commit()

        run = chat_service.ToolRunner(session, user)
        out = await run(
            "set_vacancy_status", {"vacancy_id": str(vacancy.id), "status": "applied"}
        )
        await session.commit()

        assert out["status"] == "pending_confirmation"
        # Asked for explicitly: `refresh` expires the loaded relationship, and
        # touching it afterwards would try to load it synchronously.
        await session.refresh(vacancy, ["application"])
        assert vacancy.application is None

    async def test_saving_a_vacancy_does_not_save_it_yet(self, session):
        user = await _user(session)
        vacancy = await _vacancy(session, user)
        await session.commit()

        run = chat_service.ToolRunner(session, user)
        await run("save_vacancy", {"vacancy_id": str(vacancy.id), "saved": True})
        await session.commit()

        await session.refresh(vacancy)
        assert vacancy.is_saved is False

    async def test_an_unknown_status_is_refused_before_a_proposal_exists(self, session):
        user = await _user(session)
        vacancy = await _vacancy(session, user)
        await session.commit()

        run = chat_service.ToolRunner(session, user)
        out = await run(
            "set_vacancy_status", {"vacancy_id": str(vacancy.id), "status": "нанят"}
        )
        await session.commit()

        assert "error" in out
        assert (await session.execute(select(Proposal))).scalars().first() is None

    async def test_the_proposal_records_what_it_would_change(self, session):
        """The diff on screen is built from what was true when it was made."""
        user = await _user(session)
        vacancy = await _vacancy(session, user)
        # set_status reads vacancy.application, so the relationship is loaded
        # explicitly first: a lazy load would run synchronously under asyncio.
        await session.refresh(vacancy, ["application"])
        set_status(vacancy, "applied")
        await session.commit()

        run = chat_service.ToolRunner(session, user)
        await run(
            "set_vacancy_status", {"vacancy_id": str(vacancy.id), "status": "interview"}
        )
        await session.commit()

        stored = (await session.execute(select(Proposal))).scalar_one()
        assert stored.current == {"status": "applied"}
        assert stored.proposed == {"status": "interview"}

    async def test_an_unknown_tool_is_reported(self, session):
        user = await _user(session)
        await session.commit()
        run = chat_service.ToolRunner(session, user)
        assert "error" in await run("drop_database", {})


class TestRevisionChanges:
    """What the history list says a version changed."""

    def _resume(self, profile, filename="cv.md"):
        return Resume(user_id=uuid.uuid4(), filename=filename, raw_text="", profile=profile)

    def test_an_upload_names_its_file(self):
        assert revisions.changes(self._resume({}), None) == ["Загружен файл «cv.md»"]

    def test_added_and_removed_skills_and_achievements_are_listed(self):
        parent = self._resume({
            "skills": ["Python", "Go"],
            "experience": [{"company": "Berezka VPN", "achievements": ["SPA"]}],
        })
        child = self._resume({
            "skills": ["Python", "Claude Code"],
            "experience": [{"company": "Berezka VPN", "achievements": ["SPA", "Claude Code"]}],
        })
        assert revisions.changes(child, parent) == [
            "+ навык: Claude Code",
            "− навык: Go",
            "+ Berezka VPN: Claude Code",
        ]
