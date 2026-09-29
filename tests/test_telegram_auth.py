"""Login and linking through Telegram Login (OpenID Connect).

Telegram is never called: tokens are signed here with a throwaway RSA key,
and the key lookup that would fetch Telegram's JWKS is replaced by it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import telegram_login
from app.core.config import get_settings
from app.core.telegram_login import ISSUER, issue_nonce, verify_id_token
from tests.dbmark import requires_db

BOT_ID = 8606493609
BOT_TOKEN = f"{BOT_ID}:test-only-bot-token"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def id_token(key=_KEY, *, nonce: str | None = "issue", **claims) -> str:
    """An id_token shaped like Telegram's example, signed with `key`."""
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "iss": ISSUER,
        "aud": str(BOT_ID),
        "sub": "1234123412341234123",
        "iat": now,
        "exp": now + timedelta(hours=1),
        "id": 42,
        "name": "Макс",
        "preferred_username": "max",
    }
    if nonce is not None:
        payload["nonce"] = issue_nonce() if nonce == "issue" else nonce
    payload.update(claims)
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test"})


@pytest.fixture(autouse=True)
def telegram_keys(monkeypatch):
    """Telegram's JWKS, as far as the code under test can tell."""
    monkeypatch.setattr(telegram_login, "_signing_key", lambda token: _KEY.public_key())


class TestIdToken:
    def test_a_genuine_token_names_its_user(self):
        identity = verify_id_token(id_token(), BOT_ID)
        assert identity is not None
        assert (identity.id, identity.name) == (42, "@max")

    def test_the_name_falls_back_when_there_is_no_handle(self):
        identity = verify_id_token(id_token(preferred_username=None), BOT_ID)
        assert identity is not None and identity.name == "Макс"

    def test_a_token_signed_by_someone_else_is_refused(self):
        assert verify_id_token(id_token(_OTHER_KEY), BOT_ID) is None

    def test_a_token_for_another_bot_is_refused(self):
        """Genuine and Telegram-signed, but minted for someone else's site."""
        assert verify_id_token(id_token(aud="111"), BOT_ID) is None

    def test_a_numeric_aud_is_accepted(self):
        """Telegram's docs show aud both ways; PyJWT alone rejects the number."""
        assert verify_id_token(id_token(aud=BOT_ID), BOT_ID) is not None

    def test_another_issuer_is_refused(self):
        assert verify_id_token(id_token(iss="https://evil.example"), BOT_ID) is None

    def test_an_expired_token_is_refused(self):
        past = datetime.now(UTC) - timedelta(hours=2)
        token = id_token(iat=past, exp=past + timedelta(hours=1))
        assert verify_id_token(token, BOT_ID) is None

    def test_a_token_without_our_nonce_is_refused(self):
        assert verify_id_token(id_token(nonce=None), BOT_ID) is None
        assert verify_id_token(id_token(nonce="made-up"), BOT_ID) is None

    def test_a_stale_nonce_is_refused(self):
        old = issue_nonce(now=datetime.now(UTC) - timedelta(hours=2))
        assert verify_id_token(id_token(nonce=old), BOT_ID) is None

    def test_junk_is_a_refusal_not_a_crash(self):
        assert verify_id_token("not-a-jwt", BOT_ID) is None
        assert verify_id_token(id_token(id="not-a-number"), BOT_ID) is None


class TestClientId:
    def test_it_is_the_bot_id_from_the_token(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)
        monkeypatch.setattr(settings, "telegram_client_id", None)
        assert telegram_login.client_id() == BOT_ID

    def test_an_explicit_one_wins(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)
        monkeypatch.setattr(settings, "telegram_client_id", 7)
        assert telegram_login.client_id() == 7

    def test_nothing_configured_means_off(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "telegram_bot_token", "")
        monkeypatch.setattr(settings, "telegram_client_id", None)
        assert telegram_login.client_id() is None


@pytest.fixture
def bot(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "telegram_client_id", None)
    return settings


def body(token: str | None = None) -> dict[str, str]:
    return {"id_token": token or id_token()}


@requires_db
class TestTelegramEndpoints:
    async def test_without_a_bot_the_button_is_off(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "telegram_bot_token", "")
        monkeypatch.setattr(get_settings(), "telegram_client_id", None)
        r = await client.get("/api/auth/telegram")
        assert r.json() == {"client_id": None, "nonce": None}
        assert (await client.post("/api/auth/telegram", json=body())).status_code == 404

    async def test_config_gives_the_client_id_and_a_nonce_without_a_login(self, client, bot):
        r = await client.get("/api/auth/telegram")
        assert r.status_code == 200
        assert r.json()["client_id"] == BOT_ID
        assert r.json()["nonce"]

    async def test_the_issued_nonce_is_accepted(self, client, bot):
        nonce = (await client.get("/api/auth/telegram")).json()["nonce"]
        assert verify_id_token(id_token(nonce=nonce), BOT_ID) is not None

    async def test_an_unlinked_telegram_does_not_create_an_account(self, client, bot):
        r = await client.post("/api/auth/telegram", json=body())
        assert r.status_code == 401
        assert "не привязан" in r.json()["detail"]

    async def test_linked_telegram_logs_into_the_same_account(
        self, client, bot, user, auth_headers
    ):
        linked = await client.post("/api/auth/telegram/link", json=body(), headers=auth_headers)
        assert linked.status_code == 200
        assert linked.json()["telegram_name"] == "@max"

        r = await client.post("/api/auth/telegram", json=body())
        assert r.status_code == 200
        token = r.json()["access_token"]
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["email"] == user.email

    async def test_a_forged_token_links_nothing(self, client, bot, auth_headers):
        forged = body(id_token(_OTHER_KEY))
        r = await client.post("/api/auth/telegram/link", json=forged, headers=auth_headers)
        assert r.status_code == 401
        me = await client.get("/api/auth/me", headers=auth_headers)
        assert me.json()["telegram_name"] is None

    async def test_one_telegram_cannot_be_taken_from_another_account(
        self, client, bot, session, auth_headers
    ):
        from app.models import User

        session.add(User(email="other@example.com", password_hash="x", telegram_id=42))
        await session.commit()
        r = await client.post("/api/auth/telegram/link", json=body(), headers=auth_headers)
        assert r.status_code == 409

    async def test_unlinking_stops_telegram_login(self, client, bot, auth_headers):
        await client.post("/api/auth/telegram/link", json=body(), headers=auth_headers)
        r = await client.delete("/api/auth/telegram/link", headers=auth_headers)
        assert r.json()["telegram_name"] is None
        assert (await client.post("/api/auth/telegram", json=body())).status_code == 401

    async def test_a_disabled_account_cannot_log_in_by_telegram(
        self, client, bot, session, user, auth_headers
    ):
        await client.post("/api/auth/telegram/link", json=body(), headers=auth_headers)
        user.is_active = False
        await session.commit()
        assert (await client.post("/api/auth/telegram", json=body())).status_code == 401

    async def test_telegram_down_is_ours_to_report_not_a_failed_login(
        self, client, bot, monkeypatch
    ):
        def unreachable(token):
            raise telegram_login.TelegramUnavailable("timeout")

        monkeypatch.setattr(telegram_login, "_signing_key", unreachable)
        r = await client.post("/api/auth/telegram", json=body())
        assert r.status_code == 503
