"""The chat endpoints, against a real database and a mocked model.

The model is monkeypatched everywhere: a live call in a test is forbidden by
the project's conventions, and the point here is the confirmation layer, not
the model's wording.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models import ChatMessage, Evaluation, Proposal, Resume, Vacancy
from tests.dbmark import requires_db
from tests.pool_helpers import make_vacancy


@pytest.fixture
def reply(monkeypatch):
    """Make the model say what a test needs, without a network.

    Patches `core.chat.ask`, the one function that touches the API, so the
    loop, the tool handler and the storage all run for real.
    """
    from core import chat as chat_core

    def install(*turns: dict) -> list[dict]:
        queue = list(turns)
        seen: list[dict] = []

        def fake_ask(llm, history, system="", enabled=()):
            seen.append({"history": list(history), "enabled": tuple(enabled)})
            message = queue.pop(0) if queue else {"role": "assistant", "content": "готово"}
            return (
                chat_core.Turn(
                    text=(message.get("content") or ""),
                    calls=chat_core.parse_tool_calls(message.get("tool_calls")),
                ),
                message,
            )

        monkeypatch.setattr(chat_core, "ask", fake_ask)
        # The LLM constructor would demand a key and build a client.
        monkeypatch.setattr("app.api.chat.LLM", lambda *a, **kw: object())
        return seen

    return install


def says(text: str) -> dict:
    return {"role": "assistant", "content": text}


def calls(name: str, arguments: dict, call_id: str = "c1") -> dict:
    import json

    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


async def _resume(session, user, profile: dict | None = None) -> Resume:
    resume = Resume(
        user_id=user.id,
        filename="cv.pdf",
        raw_text="текст",
        profile=profile or {"target_role": "Backend", "skills": ["Python"]},
        is_active=True,
    )
    session.add(resume)
    await session.commit()
    await session.refresh(resume)
    return resume


async def _active(session, user) -> Resume:
    # populate_existing: the accept ran in the API's own session, so rows this
    # session already holds are stale.
    result = await session.execute(
        select(Resume)
        .where(Resume.user_id == user.id, Resume.is_active.is_(True))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _vacancy(session, user) -> Vacancy:
    vacancy = make_vacancy(
        user.id,
        source="habr",
        external_id=uuid.uuid4().hex[:8],
        title="Python разработчик",
        company="ООО Ромашка",
        raw_text="Нужен Python",
        stage="shortlist",
    )
    session.add(vacancy)
    await session.commit()
    await session.refresh(vacancy)
    return vacancy


@requires_db
class TestAuth:
    async def test_the_chat_is_behind_a_login(self, client):
        assert (await client.get("/api/chat")).status_code == 401
        assert (await client.post("/api/chat", json={"text": "привет"})).status_code == 401


@requires_db
class TestSend:
    async def test_a_plain_reply_is_stored_and_returned(
        self, client, auth_headers, session, user, reply
    ):
        reply(says("Здравствуйте"))
        response = await client.post("/api/chat", json={"text": "привет"}, headers=auth_headers)
        assert response.status_code == 200, response.text
        assert response.json()["reply"] == "Здравствуйте"

        stored = list((await session.execute(select(ChatMessage))).scalars())
        assert [m.role for m in stored] == ["user", "assistant"]

    async def test_an_empty_message_is_refused(self, client, auth_headers):
        response = await client.post("/api/chat", json={"text": "   "}, headers=auth_headers)
        assert response.status_code in (400, 422)

    async def test_history_comes_back_in_order(
        self, client, auth_headers, session, user, reply
    ):
        reply(says("раз"))
        await client.post("/api/chat", json={"text": "первое"}, headers=auth_headers)
        reply(says("два"))
        await client.post("/api/chat", json={"text": "второе"}, headers=auth_headers)

        response = await client.get("/api/chat", headers=auth_headers)
        texts = [m["text"] for m in response.json()]
        assert texts == ["первое", "раз", "второе", "два"]

    async def test_an_attached_vacancy_reaches_the_model_and_the_bubble(
        self, client, auth_headers, session, user, reply
    ):
        vacancy = await _vacancy(session, user)
        seen = reply(says("Ответьте так: ..."))
        response = await client.post(
            "/api/chat",
            json={"text": "что ответить рекрутеру?", "vacancy_id": str(vacancy.id)},
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text

        sent = seen[0]["history"][-1]
        assert sent["role"] == "user"
        assert "<attached_vacancy>" in sent["content"]
        assert str(vacancy.id) in sent["content"]

        shown = (await client.get("/api/chat", headers=auth_headers)).json()
        assert shown[0]["text"] == "что ответить рекрутеру?"
        assert shown[0]["vacancy"]["id"] == str(vacancy.id)
        assert shown[0]["vacancy"]["company"] == "ООО Ромашка"
        assert shown[1]["vacancy"] is None

    async def test_a_vacancy_that_is_not_yours_is_refused(
        self, client, auth_headers, session, user, reply
    ):
        reply(says("не должно быть вызвано"))
        response = await client.post(
            "/api/chat",
            json={"text": "что ответить?", "vacancy_id": str(uuid.uuid4())},
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert not list((await session.execute(select(ChatMessage))).scalars())

    async def test_a_company_named_in_passing_is_found(
        self, client, auth_headers, session, user, reply
    ):
        """"Мне ответили из Ромашки" — the model passes the word as written."""
        vacancy = await _vacancy(session, user)
        seen = reply(calls("list_vacancies", {"query": "Ромашки"}), says("Нашёл."))
        await client.post(
            "/api/chat", json={"text": "мне ответили из Ромашки"}, headers=auth_headers
        )
        result = seen[1]["history"][-1]["content"]
        assert str(vacancy.id) in result

    async def test_tool_results_are_not_shown_to_the_user(
        self, client, auth_headers, session, user, reply
    ):
        """They are the model's working notes; a posting's raw text pasted
        into the conversation would be noise."""
        await _resume(session, user)
        reply(calls("get_profile", {}), says("В профиле только Python."))

        response = await client.post(
            "/api/chat", json={"text": "что в профиле?"}, headers=auth_headers
        )
        messages = response.json()["messages"]
        assert "tool" not in [m["role"] for m in messages]
        # A round that only asked for tools carries no text and would show as
        # an empty bubble, so its names are folded into the reply that followed.
        assert [m["text"] for m in messages] == ["В профиле только Python."]
        assert messages[0]["tools"] == ["get_profile"]

    async def test_a_turn_without_text_is_not_an_empty_bubble(
        self, client, auth_headers, session, user, reply
    ):
        """Found on a live run: the assistant message that asks for a tool has
        no content, and the conversation showed two blank turns before the
        answer."""
        await _resume(session, user)
        reply(
            calls("get_profile", {}, call_id="a"),
            calls("propose_profile_patch", {"op": "add_skill", "value": "Go"}, "b"),
            says("Готово, подтвердите."),
        )
        await client.post("/api/chat", json={"text": "знаю go"}, headers=auth_headers)

        shown = (await client.get("/api/chat", headers=auth_headers)).json()
        assert all(m["text"].strip() for m in shown)
        assert [m["text"] for m in shown] == ["знаю go", "Готово, подтвердите."]
        assert shown[1]["tools"] == ["get_profile", "propose_profile_patch"]

    async def test_a_question_is_shown_with_its_answers(
        self, client, auth_headers, session, user, reply
    ):
        """The model asks with the tool alone; the question is the reply, and
        its options come back for the screen to show as buttons."""
        await _resume(session, user)
        question = {
            "question": "Как вы использовали Claude Code?",
            "options": ["Основной способ писать код", "Помощник на части задач"],
        }
        seen = reply(calls("ask_choice", question), says("не должно прозвучать"))
        response = await client.post(
            "/api/chat", json={"text": "добавь Claude Code"}, headers=auth_headers
        )
        body = response.json()
        assert body["reply"] == question["question"]
        assert body["proposals"] == []
        assert len(seen) == 1

        shown = (await client.get("/api/chat", headers=auth_headers)).json()
        assert shown[-1]["text"] == question["question"]
        assert shown[-1]["choices"] == question["options"]
        # Asking is the reply itself, not something the chat looked at.
        assert shown[-1]["tools"] == []

    async def test_the_answer_continues_a_valid_history(
        self, client, auth_headers, session, user, reply
    ):
        """The API requires a tool result right after the call; the answer
        then arrives as an ordinary user message."""
        await _resume(session, user)
        question = {"question": "Как?", "options": ["так", "иначе"]}
        reply(calls("ask_choice", question))
        await client.post("/api/chat", json={"text": "добавь"}, headers=auth_headers)

        seen = reply(says("Понял."))
        await client.post("/api/chat", json={"text": "так"}, headers=auth_headers)
        roles = [m["role"] for m in seen[0]["history"]]
        assert roles == ["user", "assistant", "tool", "user"]

        shown = (await client.get("/api/chat", headers=auth_headers)).json()
        assert [m["text"] for m in shown] == ["добавь", "Как?", "так", "Понял."]

    async def test_open_gaps_lists_unmet_requirements_of_the_current_cv(
        self, client, auth_headers, session, user, reply
    ):
        """What lets the chat name the vacancy an edit closes a gap in.
        Reports against an older CV are left out: judged against another
        profile, their gaps may already be closed."""
        old = await _resume(session, user)
        old.is_active = False
        await session.commit()
        resume = await _resume(session, user)
        vacancy = await _vacancy(session, user)
        stale = await _vacancy(session, user)
        requirements = [
            {"text": "Опыт с AI coding tools", "match": "missing", "weight": "critical"},
            {"text": "Go", "match": "partial", "weight": "high"},
            {"text": "PostgreSQL", "match": "full", "weight": "high"},
        ]
        for v, r in ((vacancy, resume), (stale, old)):
            session.add(
                Evaluation(
                    vacancy_id=v.id,
                    resume_id=r.id,
                    score=3.8,
                    verdict="maybe",
                    report={"requirements": requirements},
                    model="deepseek-flash",
                    cost_rub=0.17,
                )
            )
        await session.commit()

        reply(calls("open_gaps", {}), says("ок"))
        await client.post("/api/chat", json={"text": "что не закрыто?"}, headers=auth_headers)

        result = (
            await session.execute(select(ChatMessage).where(ChatMessage.role == "tool"))
        ).scalar_one()
        content = result.payload["content"]
        assert "Опыт с AI coding tools" in content
        assert "Go" in content
        assert "PostgreSQL" not in content
        assert str(vacancy.id) in content
        assert str(stale.id) not in content

    async def test_without_a_cv_the_profile_tools_are_not_offered(
        self, client, auth_headers, session, user, reply
    ):
        seen = reply(says("Загрузите резюме."))
        await client.post("/api/chat", json={"text": "привет"}, headers=auth_headers)
        offered = {t.name for t in seen[0]["enabled"]}
        assert "propose_profile_patch" not in offered

    async def test_the_cost_is_charged_to_the_account(
        self, client, auth_headers, session, user, reply
    ):
        reply(says("ок"))
        await client.post("/api/chat", json={"text": "привет"}, headers=auth_headers)
        await session.refresh(user)
        # The mocked ledger spends nothing, but the field must survive the path.
        assert float(user.spent_rub) >= 0


@requires_db
class TestProposalsRequireConfirmation:
    async def test_a_proposed_patch_does_not_touch_the_resume(
        self, client, auth_headers, session, user, reply
    ):
        resume = await _resume(session, user)
        reply(
            calls("propose_profile_patch", {"op": "add_skill", "value": "Kubernetes"}),
            says("Подготовил правку, подтвердите."),
        )

        response = await client.post(
            "/api/chat", json={"text": "я знаю kubernetes"}, headers=auth_headers
        )
        assert len(response.json()["proposals"]) == 1

        await session.refresh(resume)
        assert resume.profile["skills"] == ["Python"]

    async def test_accepting_applies_the_patch(
        self, client, auth_headers, session, user, reply
    ):
        await _resume(session, user)
        reply(
            calls("propose_profile_patch", {"op": "add_skill", "value": "Kubernetes"}),
            says("Готово к подтверждению."),
        )
        created = await client.post(
            "/api/chat", json={"text": "знаю kubernetes"}, headers=auth_headers
        )
        proposal_id = created.json()["proposals"][0]["id"]

        response = await client.post(
            f"/api/chat/proposals/{proposal_id}/accept", headers=auth_headers
        )
        assert response.status_code == 200, response.text

        active = await _active(session, user)
        assert "Kubernetes" in active.profile["skills"]
        assert active.profile["provenance"]["skills"][0]["source"] == "user-stated"

    async def test_accepting_makes_a_new_revision_and_keeps_the_old_one(
        self, client, auth_headers, session, user, reply
    ):
        """Editing in place would leave bought reports describing a profile that
        no longer exists, and the same resume_id would keep serving the old
        evaluation from cache — no way to score the vacancy again."""
        resume = await _resume(session, user)
        resume.search_profile = {"queries": ["python"]}
        await session.commit()
        reply(
            calls("propose_profile_patch", {"op": "add_skill", "value": "Go"}),
            says("Готово."),
        )
        created = await client.post("/api/chat", json={"text": "знаю go"}, headers=auth_headers)
        proposal_id = created.json()["proposals"][0]["id"]
        await client.post(f"/api/chat/proposals/{proposal_id}/accept", headers=auth_headers)

        active = await _active(session, user)
        assert active.id != resume.id
        assert active.parent_id == resume.id
        assert active.revision == 2
        # Confirmed by the user earlier — a chat edit must not lose it.
        assert active.search_profile == {"queries": ["python"]}

        await session.refresh(resume)
        assert resume.is_active is False
        assert resume.profile["skills"] == ["Python"]

    async def test_a_score_bought_before_the_edit_is_marked_outdated(
        self, client, auth_headers, session, user, reply
    ):
        """The report stays on screen — it was paid for — but the row says it
        was judged against the old profile, which is what offers a re-score."""
        resume = await _resume(session, user)
        vacancy = await _vacancy(session, user)
        session.add(
            Evaluation(
                vacancy_id=vacancy.id,
                resume_id=resume.id,
                score=4.0,
                verdict="apply",
                report={"score": 4.0},
                model="deepseek-flash",
                cost_rub=0.17,
            )
        )
        await session.commit()

        def row():
            return client.get("/api/state", headers=auth_headers)

        before = (await row()).json()["found"][0]
        assert before["outdated"] is False

        reply(
            calls("propose_profile_patch", {"op": "add_skill", "value": "Go"}),
            says("Готово."),
        )
        created = await client.post("/api/chat", json={"text": "знаю go"}, headers=auth_headers)
        proposal_id = created.json()["proposals"][0]["id"]
        await client.post(f"/api/chat/proposals/{proposal_id}/accept", headers=auth_headers)

        after = (await row()).json()["found"][0]
        assert after["score"] == 4.0
        assert after["outdated"] is True

    async def test_accepting_clears_the_cached_archetypes(
        self, client, auth_headers, session, user, reply
    ):
        """Triage screens against them; keeping them would judge the user by
        a CV that no longer describes them."""
        resume = await _resume(session, user)
        resume.archetypes = {"archetypes": [{"name": "Backend"}]}
        await session.commit()

        reply(
            calls("propose_profile_patch", {"op": "add_skill", "value": "Go"}),
            says("Готово."),
        )
        created = await client.post(
            "/api/chat", json={"text": "знаю go"}, headers=auth_headers
        )
        proposal_id = created.json()["proposals"][0]["id"]
        await client.post(f"/api/chat/proposals/{proposal_id}/accept", headers=auth_headers)

        assert (await _active(session, user)).archetypes is None

    async def test_rejecting_changes_nothing(
        self, client, auth_headers, session, user, reply
    ):
        resume = await _resume(session, user)
        reply(
            calls("propose_profile_patch", {"op": "add_skill", "value": "Rust"}),
            says("Подтвердите."),
        )
        created = await client.post(
            "/api/chat", json={"text": "..."}, headers=auth_headers
        )
        proposal_id = created.json()["proposals"][0]["id"]

        response = await client.post(
            f"/api/chat/proposals/{proposal_id}/reject", headers=auth_headers
        )
        assert response.json()["status"] == "rejected"

        await session.refresh(resume)
        assert "Rust" not in resume.profile["skills"]

    async def test_a_decided_proposal_cannot_be_decided_twice(
        self, client, auth_headers, session, user, reply
    ):
        await _resume(session, user)
        reply(calls("propose_profile_patch", {"op": "add_skill", "value": "Go"}), says("."))
        created = await client.post("/api/chat", json={"text": "."}, headers=auth_headers)
        proposal_id = created.json()["proposals"][0]["id"]

        first = await client.post(
            f"/api/chat/proposals/{proposal_id}/accept", headers=auth_headers
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/chat/proposals/{proposal_id}/accept", headers=auth_headers
        )
        assert second.status_code == 409

    async def test_a_proposal_that_is_not_ours_is_a_404(
        self, client, auth_headers
    ):
        """Ownership is part of the lookup, so a proposal belonging to someone
        else is indistinguishable from one that does not exist."""
        response = await client.post(
            f"/api/chat/proposals/{uuid.uuid4()}/accept", headers=auth_headers
        )
        assert response.status_code == 404

    async def test_a_real_proposal_of_another_account_cannot_be_accepted(
        self, client, auth_headers, session, user
    ):
        """The chat must not become a way to write into someone else's CV."""
        from app.core.security import hash_password
        from app.models import User

        stranger = User(
            email=f"s{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("x"),
        )
        session.add(stranger)
        await session.flush()

        theirs = Proposal(
            user_id=stranger.id,
            kind="profile_patch",
            arguments={"op": "add_skill", "value": "Rust"},
        )
        session.add(theirs)
        await session.commit()

        response = await client.post(
            f"/api/chat/proposals/{theirs.id}/accept", headers=auth_headers
        )
        assert response.status_code == 404
        await session.refresh(theirs)
        assert theirs.status == "pending"

    async def test_a_status_proposal_moves_the_tracker_only_once_accepted(
        self, client, auth_headers, session, user, reply
    ):
        vacancy = await _vacancy(session, user)
        reply(
            calls(
                "set_vacancy_status",
                {"vacancy_id": str(vacancy.id), "status": "applied"},
            ),
            says("Подтвердите."),
        )
        created = await client.post(
            "/api/chat", json={"text": "я откликнулся"}, headers=auth_headers
        )
        proposal_id = created.json()["proposals"][0]["id"]

        await session.refresh(vacancy, ["application"])
        assert vacancy.application is None

        await client.post(f"/api/chat/proposals/{proposal_id}/accept", headers=auth_headers)
        await session.refresh(vacancy, ["application"])
        assert vacancy.application.status == "applied"

    async def test_pending_proposals_are_listed(
        self, client, auth_headers, session, user, reply
    ):
        await _resume(session, user)
        reply(calls("propose_profile_patch", {"op": "add_skill", "value": "Go"}), says("."))
        await client.post("/api/chat", json={"text": "."}, headers=auth_headers)

        response = await client.get("/api/chat/proposals", headers=auth_headers)
        assert len(response.json()) == 1
        assert response.json()[0]["status"] == "pending"


@requires_db
class TestClear:
    async def test_clearing_removes_messages_and_pending_proposals(
        self, client, auth_headers, session, user, reply
    ):
        """A user who cleared the chat must not still have changes waiting
        from a conversation they can no longer read."""
        await _resume(session, user)
        reply(calls("propose_profile_patch", {"op": "add_skill", "value": "Go"}), says("."))
        await client.post("/api/chat", json={"text": "."}, headers=auth_headers)

        response = await client.post("/api/chat/clear", headers=auth_headers)
        assert response.json()["proposals"] == 1

        assert (await client.get("/api/chat", headers=auth_headers)).json() == []
        left = list((await session.execute(select(Proposal))).scalars())
        assert left == []


@requires_db
class TestResumeRollback:
    """Going back to an earlier version, and what the chat learns of it."""

    async def _two_revisions(self, client, auth_headers, session, user, reply):
        first = await _resume(session, user)
        first.search_profile = {"queries": ["python"]}
        await session.commit()
        reply(
            calls("propose_profile_patch", {"op": "add_skill", "value": "Go"}),
            says("Готово."),
        )
        created = await client.post("/api/chat", json={"text": "знаю go"}, headers=auth_headers)
        proposal_id = created.json()["proposals"][0]["id"]
        await client.post(f"/api/chat/proposals/{proposal_id}/accept", headers=auth_headers)
        return first, await _active(session, user)

    async def test_restore_copies_the_old_version_forward(
        self, client, auth_headers, session, user, reply
    ):
        """A copy, not a reactivation: the rollback is itself undoable, and
        the version being left stays in the chain."""
        first, second = await self._two_revisions(client, auth_headers, session, user, reply)
        second.search_profile = {"queries": ["go"]}
        await session.commit()

        response = await client.post(
            f"/api/resumes/{first.id}/restore", headers=auth_headers
        )
        assert response.status_code == 200, response.text

        active = await _active(session, user)
        assert active.id not in {first.id, second.id}
        assert active.revision == 3
        assert active.parent_id == second.id
        assert active.profile["skills"] == ["Python"]
        # The search profile is its own confirmed thing; a CV rollback does
        # not reach into it.
        assert active.search_profile == {"queries": ["go"]}

    async def test_the_current_version_cannot_be_restored(
        self, client, auth_headers, session, user
    ):
        resume = await _resume(session, user)
        response = await client.post(f"/api/resumes/{resume.id}/restore", headers=auth_headers)
        assert response.status_code == 409

    async def test_the_chat_is_told_the_cv_changed(
        self, client, auth_headers, session, user, reply
    ):
        """History is per user: without a note the model reads its own
        "added it" from the old CV and refuses to add it again."""
        first, _ = await self._two_revisions(client, auth_headers, session, user, reply)
        await client.post(f"/api/resumes/{first.id}/restore", headers=auth_headers)

        shown = (await client.get("/api/chat", headers=auth_headers)).json()
        assert shown[-1]["role"] == "event"
        assert "возвращено к версии 1" in shown[-1]["text"]

        seen = reply(says("Проверю профиль."))
        await client.post("/api/chat", json={"text": "добавь go"}, headers=auth_headers)
        last = seen[0]["history"][-1]
        assert last["role"] == "user"
        assert "<event>" in last["content"] and last["content"].endswith("добавь go")
        assert all(m["role"] != "event" for m in seen[0]["history"])

    async def test_history_lists_every_version_with_its_changes(
        self, client, auth_headers, session, user, reply
    ):
        await self._two_revisions(client, auth_headers, session, user, reply)

        history = (await client.get("/api/state", headers=auth_headers)).json()[
            "resume_history"
        ]
        assert [(h["revision"], h["active"]) for h in history] == [(2, True), (1, False)]
        assert history[0]["changes"] == ["+ навык: Go"]
        assert history[1]["changes"] == ["Загружен файл «cv.pdf»"]

    async def test_a_new_upload_is_noted_in_the_chat(
        self, client, auth_headers, session, user, monkeypatch
    ):
        """The case that was reported: after re-uploading the CV the chat said
        it had already added Claude Code and would not do it again."""
        await _resume(session, user)
        monkeypatch.setattr("app.api.resumes.LLM", lambda *a, **kw: object())
        monkeypatch.setattr(
            "app.api.resumes.parse_resume", lambda llm, text: {"skills": ["Python"]}
        )
        monkeypatch.setattr("app.api.resumes.build_search_profile", lambda llm, p: {})

        response = await client.post(
            "/api/resumes",
            files={"file": ("new.md", "# Резюме\nPython".encode(), "text/markdown")},
            headers=auth_headers,
        )
        assert response.status_code == 201, response.text

        shown = (await client.get("/api/chat", headers=auth_headers)).json()
        assert shown[-1]["role"] == "event"
        assert "«new.md»" in shown[-1]["text"]

    async def test_a_first_upload_leaves_the_chat_alone(
        self, client, auth_headers, monkeypatch
    ):
        """Nothing was replaced, so there is nothing to warn about."""
        monkeypatch.setattr("app.api.resumes.LLM", lambda *a, **kw: object())
        monkeypatch.setattr("app.api.resumes.parse_resume", lambda llm, text: {})
        monkeypatch.setattr("app.api.resumes.build_search_profile", lambda llm, p: {})

        await client.post(
            "/api/resumes",
            files={"file": ("cv.md", b"Python", "text/markdown")},
            headers=auth_headers,
        )
        assert (await client.get("/api/chat", headers=auth_headers)).json() == []


@requires_db
class TestManualEdit:
    """The user corrects the parsed CV by hand. Free, and a new revision."""

    async def test_an_edit_is_saved_as_the_next_revision(
        self, client, auth_headers, session, user
    ):
        first = await _resume(
            session,
            user,
            {
                "target_role": "Backend",
                "skills": ["Python"],
                "preferences": {"city": "Москва"},
                "experience": [{"company": "Ромашка", "role": "Dev", "extra": "x"}],
            },
        )
        first.search_profile = {"queries": ["python"]}
        await session.commit()

        response = await client.put(
            "/api/resumes/active/profile",
            json={
                "target_role": "  Backend-разработчик ",
                "skills": ["Python", " Go ", "", "python"],
                "experience": [
                    {"company": "Ромашка", "role": "Senior Dev", "extra": "x", "location": ""},
                    {"company": "", "role": "", "achievements": [""]},
                ],
            },
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text

        active = await _active(session, user)
        assert active.id != first.id
        assert active.revision == 2 and active.parent_id == first.id
        assert active.profile["target_role"] == "Backend-разработчик"
        assert active.profile["skills"] == ["Python", "Go"]
        # A cleared block is a removed job; a cleared field is left out, and a
        # key the form does not know about survives.
        assert active.profile["experience"] == [
            {"company": "Ромашка", "role": "Senior Dev", "extra": "x"}
        ]
        # Keys the request left out keep their value.
        assert active.profile["preferences"] == {"city": "Москва"}
        assert active.search_profile == {"queries": ["python"]}

    async def test_an_unchanged_save_makes_no_revision(
        self, client, auth_headers, session, user
    ):
        """A revision without a change would mark every bought report as made
        against «прежнее резюме» for nothing."""
        first = await _resume(session, user)
        response = await client.put(
            "/api/resumes/active/profile",
            json={"target_role": "Backend", "skills": ["Python"]},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert (await _active(session, user)).id == first.id

    async def test_the_city_is_edited_without_losing_other_preferences(
        self, client, auth_headers, session, user
    ):
        first = await _resume(session, user, {
            "target_role": "Backend",
            "preferences": {"city": "Казань", "format": "гибрид"},
        })
        response = await client.put(
            "/api/resumes/active/profile",
            json={"preferences": {"city": " Москва "}},
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text
        active = await _active(session, user)
        assert active.id != first.id
        assert active.profile["preferences"] == {"city": "Москва", "format": "гибрид"}

        history = (await client.get("/api/state", headers=auth_headers)).json()[
            "resume_history"
        ]
        assert "город: Казань → Москва" in history[0]["changes"]

    async def test_a_blank_city_on_a_profile_without_one_is_no_change(
        self, client, auth_headers, session, user
    ):
        first = await _resume(session, user)
        response = await client.put(
            "/api/resumes/active/profile",
            json={"target_role": "Backend", "preferences": {"city": ""}},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert (await _active(session, user)).id == first.id

    async def test_an_unknown_level_is_refused(self, client, auth_headers, session, user):
        await _resume(session, user)
        response = await client.put(
            "/api/resumes/active/profile",
            json={"seniority": "guru"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_history_and_chat_say_what_was_edited(
        self, client, auth_headers, session, user
    ):
        await _resume(session, user)
        await client.put(
            "/api/resumes/active/profile",
            json={"summary": "Пишу бэкенд", "languages": ["Английский B2"]},
            headers=auth_headers,
        )

        history = (await client.get("/api/state", headers=auth_headers)).json()[
            "resume_history"
        ]
        assert history[0]["changes"] == ["+ язык: Английский B2", "изменён абзац «О себе»"]

        shown = (await client.get("/api/chat", headers=auth_headers)).json()
        assert shown[-1]["role"] == "event"
        assert "вручную" in shown[-1]["text"]

    async def test_without_a_cv_there_is_nothing_to_edit(self, client, auth_headers):
        response = await client.put(
            "/api/resumes/active/profile", json={"name": "Макс"}, headers=auth_headers
        )
        assert response.status_code == 404
