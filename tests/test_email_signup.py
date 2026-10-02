"""Signing up by email: the letter, the link, and what the link may not do.

Resend is never called: `send_email` is replaced by a list the tests read.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.security import (
    create_access_token,
    create_email_token,
    read_access_token,
    read_email_token,
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
    match = re.search(r"/#/verify\?token=(\S+)", letter["text"])
    assert match, letter["text"]
    return match.group(1)


SIGNUP = {"email": "New@Example.com", "password": "long enough", "consent": True}


class TestTokens:
    """No database: the two token kinds must not stand in for each other."""

    def test_a_letter_token_is_not_a_login(self):
        token = create_email_token(uuid.uuid4(), "a@example.com", "hash")
        assert read_access_token(token) is None

    def test_a_login_token_is_not_a_letter_token(self):
        assert read_email_token(create_access_token(uuid.uuid4())) is None

    def test_a_letter_token_carries_its_account_and_address(self):
        user_id = uuid.uuid4()
        claims = read_email_token(create_email_token(user_id, "a@example.com", "hash"))
        assert claims is not None
        assert claims[:2] == (user_id, "a@example.com")


class TestLetter:
    def test_the_link_is_escaped_in_html(self):
        _, text, html_body = mail.verification_letter('https://x/#/verify?token=a"b')
        assert 'token=a"b' in text
        assert 'a"b' not in html_body

    async def test_without_a_key_nothing_is_sent(self, monkeypatch):
        monkeypatch.setattr(mail, "mail_enabled", lambda: False)
        with pytest.raises(mail.MailUnavailable):
            await mail.send_email("a@example.com", "s", "t", "h")

    async def test_a_refusal_from_resend_is_unavailable_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(mail, "mail_enabled", lambda: True)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "domain is not verified"})

        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            mail.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )
        with pytest.raises(mail.MailUnavailable):
            await mail.send_email("a@example.com", "s", "t", "h")


@requires_db
class TestSignup:
    async def test_off_without_mail(self, client, monkeypatch):
        monkeypatch.setattr(mail, "mail_enabled", lambda: False)
        assert (await client.get("/api/auth/signup")).json() == {"enabled": False}
        r = await client.post("/api/auth/signup", json=SIGNUP)
        assert r.status_code == 404

    async def test_on_with_mail(self, client, outbox):
        assert (await client.get("/api/auth/signup")).json() == {"enabled": True}

    async def test_signup_sends_a_letter_and_login_waits_for_it(self, client, outbox):
        r = await client.post("/api/auth/signup", json=SIGNUP)
        assert r.status_code == 202
        assert r.json()["email"] == "new@example.com"
        assert [letter["to"] for letter in outbox] == ["new@example.com"]

        login = await client.post(
            "/api/auth/login", json={"email": "new@example.com", "password": "long enough"}
        )
        assert login.status_code == 403
        assert "подтверждена" in login.json()["detail"]

    async def test_the_link_logs_in_and_opens_the_password_login(self, client, outbox):
        await client.post("/api/auth/signup", json=SIGNUP)
        r = await client.post("/api/auth/verify", json={"token": link_token(outbox[0])})
        assert r.status_code == 200
        me = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
        )
        assert me.json()["email"] == "new@example.com"

        login = await client.post(
            "/api/auth/login", json={"email": "new@example.com", "password": "long enough"}
        )
        assert login.status_code == 200

    async def test_opening_the_link_twice_is_fine(self, client, outbox):
        await client.post("/api/auth/signup", json=SIGNUP)
        token = link_token(outbox[0])
        assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 200
        assert (await client.post("/api/auth/verify", json={"token": token})).status_code == 200

    async def test_signup_without_consent_is_refused_and_sends_nothing(
        self, client, outbox
    ):
        r = await client.post("/api/auth/signup", json={**SIGNUP, "consent": False})
        assert r.status_code == 400
        assert outbox == []

    async def test_signup_records_consent(self, client, outbox):
        await client.post("/api/auth/signup", json=SIGNUP)
        r = await client.post("/api/auth/verify", json={"token": link_token(outbox[0])})
        me = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
        )
        assert me.json()["consent_at"] is not None

    async def test_a_short_password_is_refused(self, client, outbox):
        r = await client.post(
            "/api/auth/signup", json={"email": "a@example.com", "password": "short"}
        )
        assert r.status_code == 422
        assert outbox == []

    async def test_a_confirmed_address_looks_the_same_and_keeps_its_password(
        self, client, outbox, user
    ):
        """Same answer as a new signup, no letter, and the owner's password stands."""
        r = await client.post(
            "/api/auth/signup", json={"email": user.email, "password": "hijacker pass", "consent": True}
        )
        assert r.status_code == 202
        assert outbox == []
        login = await client.post(
            "/api/auth/login", json={"email": user.email, "password": "correct horse"}
        )
        assert login.status_code == 200

    async def test_a_repeat_right_away_is_throttled(self, client, outbox):
        await client.post("/api/auth/signup", json=SIGNUP)
        again = await client.post("/api/auth/signup", json=SIGNUP)
        assert again.status_code == 429
        assert len(outbox) == 1

    async def test_a_repeat_later_replaces_the_password_and_voids_the_old_link(
        self, client, session, outbox
    ):
        from sqlalchemy import update

        from app.models import User

        await client.post("/api/auth/signup", json=SIGNUP)
        await session.execute(
            update(User).values(verification_sent_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        await session.commit()
        r = await client.post(
            "/api/auth/signup", json={"email": SIGNUP["email"], "password": "second password", "consent": True}
        )
        assert r.status_code == 202
        assert len(outbox) == 2

        old = await client.post("/api/auth/verify", json={"token": link_token(outbox[0])})
        assert old.status_code == 400
        new = await client.post("/api/auth/verify", json={"token": link_token(outbox[1])})
        assert new.status_code == 200
        login = await client.post(
            "/api/auth/login", json={"email": "new@example.com", "password": "second password", "consent": True}
        )
        assert login.status_code == 200

    async def test_a_failed_send_leaves_no_account(self, client, monkeypatch):
        async def refuse(*args, **kwargs):
            raise mail.MailUnavailable("quota")

        monkeypatch.setattr(mail, "mail_enabled", lambda: True)
        monkeypatch.setattr(mail, "send_email", refuse)
        r = await client.post("/api/auth/signup", json=SIGNUP)
        assert r.status_code == 503

        login = await client.post(
            "/api/auth/login", json={"email": "new@example.com", "password": "long enough"}
        )
        assert login.status_code == 401

    async def test_junk_and_login_tokens_are_refused(self, client, auth_headers):
        assert (await client.post("/api/auth/verify", json={"token": "junk"})).status_code == 400
        access = auth_headers["Authorization"].removeprefix("Bearer ")
        r = await client.post("/api/auth/verify", json={"token": access})
        assert r.status_code == 400

    async def test_a_disabled_account_is_not_let_in_by_the_link(self, client, session, outbox):
        from sqlalchemy import update

        from app.models import User

        await client.post("/api/auth/signup", json=SIGNUP)
        await session.execute(update(User).values(is_active=False))
        await session.commit()
        r = await client.post("/api/auth/verify", json={"token": link_token(outbox[0])})
        assert r.status_code == 401
