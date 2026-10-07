"""Resetting a lost password by a link from a letter.

Resend is never called: `send_email` is replaced by a list the tests read.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    ALGORITHM,
    create_access_token,
    create_email_token,
    create_reset_token,
    read_email_token,
    read_reset_token,
)
from app.services import mail
from tests.dbmark import requires_db


@pytest.fixture
def outbox(monkeypatch) -> list[dict[str, str]]:
    letters: list[dict[str, str]] = []

    async def fake_send(to: str, subject: str, text: str, html_body: str) -> None:
        letters.append({"to": to, "subject": subject, "text": text, "html": html_body})

    monkeypatch.setattr(mail, "mail_enabled", lambda: True)
    monkeypatch.setattr(mail, "send_email", fake_send)
    return letters


def link_token(letter: dict[str, str]) -> str:
    match = re.search(r"/reset-password\?token=(\S+)", letter["text"])
    assert match, letter["text"]
    return match.group(1)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestTokens:
    """No database: a reset link is neither a confirmation nor a login."""

    def test_a_confirmation_link_does_not_reset(self):
        token = create_email_token(uuid.uuid4(), "a@example.com", "hash")
        assert read_reset_token(token) is None

    def test_a_reset_link_does_not_confirm(self):
        token = create_reset_token(uuid.uuid4(), "a@example.com", "hash")
        assert read_email_token(token) is None

    def test_a_login_token_does_not_reset(self):
        assert read_reset_token(create_access_token(uuid.uuid4())) is None

    def test_the_link_is_escaped_in_html(self):
        _, text, html_body = mail.password_reset_letter('https://x/reset-password?token=a"b')
        assert 'token=a"b' in text
        assert 'a"b' not in html_body


@requires_db
class TestReset:
    async def test_off_without_mail(self, client, user, monkeypatch):
        monkeypatch.setattr(mail, "mail_enabled", lambda: False)
        r = await client.post("/api/auth/password-reset", json={"email": user.email})
        assert r.status_code == 404

    async def test_the_link_sets_a_new_password_and_logs_in(self, client, user, outbox):
        r = await client.post("/api/auth/password-reset", json={"email": user.email.upper()})
        assert r.status_code == 202
        assert [letter["to"] for letter in outbox] == [user.email]

        done = await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": link_token(outbox[0]), "password": "brand new one"},
        )
        assert done.status_code == 200
        me = await client.get("/api/auth/me", headers=bearer(done.json()["access_token"]))
        assert me.status_code == 200

        old = await client.post(
            "/api/auth/login", json={"email": user.email, "password": "correct horse"}
        )
        assert old.status_code == 401
        new = await client.post(
            "/api/auth/login", json={"email": user.email, "password": "brand new one"}
        )
        assert new.status_code == 200

    async def test_the_link_works_once(self, client, user, outbox):
        await client.post("/api/auth/password-reset", json={"email": user.email})
        token = link_token(outbox[0])
        body = {"token": token, "password": "brand new one"}
        assert (await client.post("/api/auth/password-reset/confirm", json=body)).status_code == 200
        again = await client.post("/api/auth/password-reset/confirm", json=body)
        assert again.status_code == 400

    async def test_an_unknown_address_gets_the_same_answer_and_no_letter(
        self, client, user, outbox
    ):
        known = await client.post("/api/auth/password-reset", json={"email": user.email})
        unknown = await client.post(
            "/api/auth/password-reset", json={"email": "nobody@example.com"}
        )
        assert unknown.status_code == known.status_code == 202
        assert unknown.json()["detail"] == known.json()["detail"]
        assert len(outbox) == 1

    async def test_a_repeat_right_away_is_silent(self, client, user, outbox):
        await client.post("/api/auth/password-reset", json={"email": user.email})
        again = await client.post("/api/auth/password-reset", json={"email": user.email})
        assert again.status_code == 202
        assert len(outbox) == 1

    async def test_a_disabled_account_gets_no_letter(self, client, session, user, outbox):
        user.is_active = False
        await session.commit()
        r = await client.post("/api/auth/password-reset", json={"email": user.email})
        assert r.status_code == 202
        assert outbox == []

    async def test_a_short_password_is_refused(self, client, user, outbox):
        await client.post("/api/auth/password-reset", json={"email": user.email})
        r = await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": link_token(outbox[0]), "password": "short"},
        )
        assert r.status_code == 422

    async def test_junk_and_confirmation_tokens_are_refused(self, client, user):
        junk = {"token": "junk", "password": "brand new one"}
        assert (await client.post("/api/auth/password-reset/confirm", json=junk)).status_code == 400
        confirmation = create_email_token(user.id, user.email, user.password_hash)
        r = await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": confirmation, "password": "brand new one"},
        )
        assert r.status_code == 400

    async def test_a_failed_send_looks_like_any_other_answer(self, client, user, monkeypatch):
        # Only a known address reaches the send: an error here would mark it.
        async def refuse(*args, **kwargs):
            raise mail.MailUnavailable("quota")

        monkeypatch.setattr(mail, "mail_enabled", lambda: True)
        monkeypatch.setattr(mail, "send_email", refuse)
        known = await client.post("/api/auth/password-reset", json={"email": user.email})
        unknown = await client.post(
            "/api/auth/password-reset", json={"email": "nobody@example.com"}
        )
        assert known.status_code == unknown.status_code == 202
        assert known.json()["detail"] == unknown.json()["detail"]

    async def test_a_reset_logs_out_older_sessions(self, client, user, outbox):
        # Issued a minute earlier: a token from the same second as the reset
        # is let through by design (iat has whole seconds).
        earlier = datetime.now(UTC) - timedelta(minutes=1)
        old = jwt.encode(
            {"sub": str(user.id), "iat": earlier, "exp": earlier + timedelta(hours=1)},
            get_settings().secret_key,
            algorithm=ALGORITHM,
        )
        assert (await client.get("/api/auth/me", headers=bearer(old))).status_code == 200

        await client.post("/api/auth/password-reset", json={"email": user.email})
        await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": link_token(outbox[0]), "password": "brand new one"},
        )
        assert (await client.get("/api/auth/me", headers=bearer(old))).status_code == 401

    async def test_an_unconfirmed_signup_is_confirmed_by_the_reset(self, client, session, outbox):
        from sqlalchemy import update

        from app.models import User

        await client.post(
            "/api/auth/signup",
            json={"email": "late@example.com", "password": "forgotten", "consent": True},
        )
        # Past the throttle the signup letter set.
        await session.execute(
            update(User).values(verification_sent_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        await session.commit()
        await client.post("/api/auth/password-reset", json={"email": "late@example.com"})
        assert len(outbox) == 2
        done = await client.post(
            "/api/auth/password-reset/confirm",
            json={"token": link_token(outbox[1]), "password": "remembered"},
        )
        assert done.status_code == 200
        login = await client.post(
            "/api/auth/login", json={"email": "late@example.com", "password": "remembered"}
        )
        assert login.status_code == 200
