"""Login, tokens and who may see what."""

from __future__ import annotations

import uuid

from app.core.security import (
    create_access_token,
    hash_password,
    read_access_token,
    verify_password,
)
from tests.dbmark import requires_db
from tests.pool_helpers import make_vacancy

# asyncio_mode is "auto" in pyproject, so async tests need no marker; the
# synchronous ones above must not carry a module-level asyncio mark.


class TestPasswordHashing:
    """No database needed — these are pure functions."""

    def test_bcrypt_is_what_gets_stored(self):
        """docs/03-architecture.md specifies bcrypt; pwdlib defaults to argon2."""
        assert hash_password("secret").startswith("$2b$")

    def test_a_password_verifies_against_its_own_hash(self):
        assert verify_password("secret", hash_password("secret"))

    def test_a_wrong_password_does_not(self):
        assert not verify_password("wrong", hash_password("secret"))

    def test_the_same_password_hashes_differently_each_time(self):
        """Salted: two accounts with one password must not share a hash."""
        assert hash_password("secret") != hash_password("secret")

    def test_a_damaged_stored_hash_is_a_failed_login_not_a_crash(self):
        assert not verify_password("secret", "not-a-hash")


class TestTokens:
    def test_a_token_carries_its_user_back(self):
        user_id = uuid.uuid4()
        assert read_access_token(create_access_token(user_id)) == user_id

    def test_a_tampered_token_is_rejected(self):
        token = create_access_token(uuid.uuid4())
        assert read_access_token(token[:-4] + "aaaa") is None

    def test_junk_is_rejected_rather_than_raising(self):
        assert read_access_token("garbage") is None


@requires_db
class TestLogin:
    async def test_correct_credentials_return_a_token(self, client, user):
        r = await client.post(
            "/api/auth/login", json={"email": user.email, "password": "correct horse"}
        )
        assert r.status_code == 200
        assert r.json()["access_token"]

    async def test_a_wrong_password_and_an_unknown_email_look_identical(self, client, user):
        """Different messages would reveal which addresses have accounts here."""
        wrong = await client.post(
            "/api/auth/login", json={"email": user.email, "password": "nope"}
        )
        unknown = await client.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": "nope"}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()

    async def test_email_case_does_not_matter(self, client, user):
        r = await client.post(
            "/api/auth/login",
            json={"email": user.email.upper(), "password": "correct horse"},
        )
        assert r.status_code == 200

    async def test_a_disabled_account_cannot_log_in(self, client, session, user):
        user.is_active = False
        await session.commit()
        r = await client.post(
            "/api/auth/login", json={"email": user.email, "password": "correct horse"}
        )
        assert r.status_code == 401


@requires_db
class TestProtectedRoutes:
    async def test_no_token_is_401(self, client):
        assert (await client.get("/api/auth/me")).status_code == 401

    async def test_a_valid_token_reaches_the_account(self, client, auth_headers, user):
        r = await client.get("/api/auth/me", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["email"] == user.email

    async def test_disabling_an_account_closes_a_token_already_issued(
        self, client, session, user, auth_headers
    ):
        """The token outlives the decision to switch the account off.

        Access has to stop when the flag flips, not when the token expires,
        which is why is_active is checked on every request and not only at login.
        """
        assert (await client.get("/api/auth/me", headers=auth_headers)).status_code == 200
        user.is_active = False
        await session.commit()
        assert (await client.get("/api/auth/me", headers=auth_headers)).status_code == 401

    async def test_a_token_for_a_deleted_user_is_rejected(self, client):
        token = create_access_token(uuid.uuid4())
        r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


@requires_db
class TestConsent:
    async def test_consent_is_recorded_once_and_keeps_its_first_date(
        self, client, auth_headers
    ):
        """Consent is evidence, not a checkbox: the first date is the one that counts."""
        first = await client.post("/api/auth/consent", headers=auth_headers)
        assert first.status_code == 200
        stamp = first.json()["consent_at"]
        assert stamp is not None

        again = await client.post("/api/auth/consent", headers=auth_headers)
        assert again.json()["consent_at"] == stamp


@requires_db
class TestDeleteAccount:
    async def test_deleting_takes_the_resume_and_vacancies_with_it(
        self, client, session, user, auth_headers
    ):
        """The right to erasure: nothing of the account is left behind."""
        from sqlalchemy import func, select

        from app.models import Resume, User, Vacancy

        session.add(Resume(user_id=user.id, filename="cv.pdf", raw_text="Иван", profile={}))
        session.add(make_vacancy(user.id, source="habr", external_id="1", title="Python"))
        await session.commit()
        user_id = user.id

        r = await client.delete("/api/auth/me", headers=auth_headers)

        assert r.status_code == 204
        session.expunge_all()
        for column in (User.id, Resume.user_id, Vacancy.user_id):
            count = await session.scalar(select(func.count()).where(column == user_id))
            assert count == 0, column
        assert (await client.get("/api/auth/me", headers=auth_headers)).status_code == 401

    async def test_an_admin_cannot_delete_themselves_from_the_cabinet(
        self, client, session, user, auth_headers
    ):
        user.is_admin = True
        await session.commit()

        r = await client.delete("/api/auth/me", headers=auth_headers)

        assert r.status_code == 403
        assert (await client.get("/api/auth/me", headers=auth_headers)).status_code == 200


@requires_db
class TestProfileName:
    async def test_a_new_account_has_no_name(self, client, auth_headers):
        """The cabinet falls back to the email; a made-up name would be wrong."""
        me = await client.get("/api/auth/me", headers=auth_headers)
        assert me.json()["name"] is None

    async def test_the_name_is_saved_trimmed_and_shown_in_state(self, client, auth_headers):
        r = await client.patch("/api/auth/me", headers=auth_headers, json={"name": "  Макс  "})
        assert r.status_code == 200
        assert r.json()["name"] == "Макс"
        state = await client.get("/api/state", headers=auth_headers)
        assert state.json()["user"]["name"] == "Макс"

    async def test_a_blank_name_clears_it(self, client, auth_headers):
        await client.patch("/api/auth/me", headers=auth_headers, json={"name": "Макс"})
        r = await client.patch("/api/auth/me", headers=auth_headers, json={"name": "   "})
        assert r.json()["name"] is None

    async def test_a_field_not_sent_is_not_touched(self, client, auth_headers):
        await client.patch("/api/auth/me", headers=auth_headers, json={"name": "Макс"})
        r = await client.patch("/api/auth/me", headers=auth_headers, json={})
        assert r.json()["name"] == "Макс"

    async def test_a_name_longer_than_the_column_is_refused_not_cut(self, client, auth_headers):
        r = await client.patch("/api/auth/me", headers=auth_headers, json={"name": "я" * 81})
        assert r.status_code == 422

    async def test_without_a_token_nothing_changes(self, client):
        r = await client.patch("/api/auth/me", json={"name": "Макс"})
        assert r.status_code == 401
