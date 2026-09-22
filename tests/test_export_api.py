"""The download endpoints: who may download what, and at what price.

The price is the point of most of these. A download renders JSON that was
already paid for, so it must never charge and must never require anything to
be bought again — the same rule `/api/artifacts` follows when it returns a
cached row instead of calling the model a second time.

DOCX is exercised end to end. PDF is not: WeasyPrint loads Pango/Cairo from
the OS, which CI need not have, so the PDF route is covered by asserting that
a missing renderer produces a 503 the user can read rather than a 500.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from app.models import Artifact, Evaluation, Resume, User, Vacancy
from tests.dbmark import requires_db

PROFILE = {
    "name": "Иванов Иван",
    "target_role": "Backend-инженер",
    "skills": ["Python"],
    "experience": [
        {
            "role": "Тимлид",
            "company": "Ромашка",
            "period": "2021–2025",
            "achievements": ["Сократил сборку на 40%"],
        }
    ],
}

TAILORED = {
    "summary": "Инженер с опытом.",
    "experience": [{"role": "Тимлид", "company": "Ромашка", "period": "2021", "bullets": ["CI/CD"]}],
    "skills": ["Python"],
    "changes": ["Переставил акценты на платформу"],
}

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _resume(session, user: User) -> Resume:
    resume = Resume(
        user_id=user.id,
        filename="cv.pdf",
        raw_text="текст",
        profile=PROFILE,
        is_active=True,
    )
    session.add(resume)
    await session.commit()
    await session.refresh(resume)
    return resume


async def _vacancy_with_tailored(session, user: User, resume: Resume) -> Vacancy:
    vacancy = Vacancy(
        user_id=user.id,
        source="habr",
        external_id="1",
        title="Backend-разработчик",
        company="ООО Ромашка",
        raw_text="описание",
        stage="evaluated",
        is_saved=True,
    )
    session.add(vacancy)
    await session.flush()

    evaluation = Evaluation(
        vacancy_id=vacancy.id,
        resume_id=resume.id,
        score=8.0,
        verdict="apply",
        report={"score": 8.0},
        model="deepseek-flash",
        cost_rub=Decimal("0.17"),
    )
    session.add(evaluation)
    await session.flush()

    session.add(
        Artifact(
            evaluation_id=evaluation.id,
            kind="tailor",
            content=TAILORED,
            cost_rub=Decimal("0.3"),
        )
    )
    await session.commit()
    await session.refresh(vacancy)
    return vacancy


def _text_of(body: bytes) -> str:
    import docx

    return "\n".join(p.text for p in docx.Document(BytesIO(body)).paragraphs)


@requires_db
class TestProfileExport:
    async def test_downloads_the_cv_as_docx(self, client, session, user, auth_headers):
        await _resume(session, user)

        r = await client.get("/api/export/resume.docx", headers=auth_headers)

        assert r.status_code == 200
        assert r.headers["content-type"] == DOCX_TYPE
        assert "Иванов Иван" in _text_of(r.content)

    async def test_the_filename_carries_the_name_in_utf8(self, client, session, user, auth_headers):
        """Without `filename*` the save dialog shows mojibake for a Cyrillic name."""
        await _resume(session, user)

        r = await client.get("/api/export/resume.docx", headers=auth_headers)

        disposition = r.headers["content-disposition"]
        assert "attachment" in disposition
        assert "filename*=UTF-8''" in disposition
        assert "%D0%98" in disposition  # «И» percent-encoded

    async def test_it_charges_nothing(self, client, session, user, auth_headers):
        """The JSON was paid for once; rendering it again is free forever."""
        await _resume(session, user)
        before = Decimal(str(user.spent_rub))

        await client.get("/api/export/resume.docx", headers=auth_headers)
        await client.get("/api/export/resume.docx", headers=auth_headers)

        await session.refresh(user)
        assert Decimal(str(user.spent_rub)) == before

    async def test_without_a_resume_it_says_what_to_do(self, client, auth_headers):
        r = await client.get("/api/export/resume.docx", headers=auth_headers)
        assert r.status_code == 404
        assert "резюме" in r.json()["detail"].lower()

    async def test_anonymous_is_refused(self, client, session, user):
        await _resume(session, user)
        assert (await client.get("/api/export/resume.docx")).status_code == 401

    async def test_an_unknown_format_is_rejected(self, client, session, user, auth_headers):
        await _resume(session, user)
        r = await client.get("/api/export/resume.rtf", headers=auth_headers)
        assert r.status_code == 422

    async def test_missing_system_libraries_read_as_a_server_problem(
        self, client, session, user, auth_headers, monkeypatch
    ):
        """WeasyPrint raises OSError, not ImportError, when Pango is absent.

        This exact message came from a host without libpangoft2: the route
        answered 500 while this test — which then raised ImportError — was
        green. A mock that does not match reality proves nothing.
        """
        import core.export

        def no_pango(doc):
            raise OSError(
                "cannot load library 'libpangoft2-1.0-0': "
                "libpangoft2-1.0-0: cannot open shared object file"
            )

        monkeypatch.setattr(core.export, "to_pdf", no_pango)
        await _resume(session, user)

        r = await client.get("/api/export/resume.pdf", headers=auth_headers)

        assert r.status_code == 503
        assert "PDF" in r.json()["detail"]

    async def test_a_missing_renderer_package_also_reads_as_503(
        self, client, session, user, auth_headers, monkeypatch
    ):
        """The other half: no wheel at all, so the import itself fails."""
        import core.export

        def no_weasyprint(doc):
            raise ImportError("No module named 'weasyprint'")

        monkeypatch.setattr(core.export, "to_pdf", no_weasyprint)
        await _resume(session, user)

        r = await client.get("/api/export/resume.pdf", headers=auth_headers)

        assert r.status_code == 503


@requires_db
class TestTailoredExport:
    async def test_downloads_the_bought_resume(self, client, session, user, auth_headers):
        resume = await _resume(session, user)
        vacancy = await _vacancy_with_tailored(session, user, resume)

        r = await client.get(
            f"/api/export/vacancy/{vacancy.id}/tailor.docx", headers=auth_headers
        )

        assert r.status_code == 200
        body = _text_of(r.content)
        assert "Иванов Иван" in body  # identity comes from the profile
        assert "CI/CD" in body

    async def test_the_model_notes_never_reach_the_employer(
        self, client, session, user, auth_headers
    ):
        """`changes` is the user's audit trail, not part of the CV."""
        resume = await _resume(session, user)
        vacancy = await _vacancy_with_tailored(session, user, resume)

        r = await client.get(
            f"/api/export/vacancy/{vacancy.id}/tailor.docx", headers=auth_headers
        )

        assert "акценты" not in _text_of(r.content)

    async def test_the_filename_names_the_company(self, client, session, user, auth_headers):
        """"resume.pdf" in a downloads folder tells the user nothing later."""
        resume = await _resume(session, user)
        vacancy = await _vacancy_with_tailored(session, user, resume)

        r = await client.get(
            f"/api/export/vacancy/{vacancy.id}/tailor.docx", headers=auth_headers
        )

        assert "%D0%A0%D0%BE%D0%BC%D0%B0%D1%88%D0%BA%D0%B0" in r.headers["content-disposition"]

    async def test_not_bought_yet_says_so_instead_of_an_empty_file(
        self, client, session, user, auth_headers
    ):
        await _resume(session, user)
        vacancy = Vacancy(
            user_id=user.id,
            source="habr",
            external_id="2",
            title="Без материалов",
            stage="evaluated",
        )
        session.add(vacancy)
        await session.commit()
        await session.refresh(vacancy)

        r = await client.get(
            f"/api/export/vacancy/{vacancy.id}/tailor.docx", headers=auth_headers
        )

        assert r.status_code == 404
        assert "избранном" in r.json()["detail"]

    async def test_another_users_document_is_not_downloadable(
        self, client, session, user, auth_headers
    ):
        """The vacancy id is a UUID, but guessing must not be the only defence."""
        stranger = User(
            email="stranger@example.com",
            password_hash="x",
            is_active=True,
        )
        session.add(stranger)
        await session.flush()
        stranger_resume = await _resume(session, stranger)
        stranger_vacancy = await _vacancy_with_tailored(session, stranger, stranger_resume)

        await _resume(session, user)
        r = await client.get(
            f"/api/export/vacancy/{stranger_vacancy.id}/tailor.docx", headers=auth_headers
        )

        assert r.status_code == 404
