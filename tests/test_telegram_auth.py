"""Login and linking through the Telegram Login Widget."""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.core.security import verify_telegram_login
from tests.dbmark import requires_db

BOT_TOKEN = "123456:test-only-bot-token"


def signed(token: str = BOT_TOKEN, **fields) -> dict[str, object]:
    """A widget answer signed the way Telegram signs it."""
    data: dict[str, object] = {
        "id": 42,
        "first_name": "Макс",
        "username": "max",
        "auth_date": int(time.time()),
        **fields,
    }
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(token.encode()).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return data


class TestSignature:
    def test_a_genuine_answer_names_its_user(self):
        assert verify_telegram_login(signed(), BOT_TOKEN) == 42

    def test_an_edited_field_breaks_the_signature(self):
        data = signed()
        data["id"] = 43
        assert verify_telegram_login(data, BOT_TOKEN) is None

    def test_another_bots_signature_is_not_ours(self):
        assert verify_telegram_login(signed(token="999:other"), BOT_TOKEN) is None

    def test_fields_outside_the_known_set_are_signed_too(self):
        """Dropping an unknown field would fail logins the day Telegram adds one."""
        assert verify_telegram_login(signed(photo_url="https://t.me/i/x.jpg"), BOT_TOKEN) == 42

    def test_a_stale_answer_cannot_be_replayed(self):
        data = signed(auth_date=int(time.time()) - 2 * 3600)
        assert verify_telegram_login(data, BOT_TOKEN) is None

    def test_an_answer_an_hour_old_minus_a_minute_still_works(self):
        now = datetime.now(UTC)
        data = signed(auth_date=int((now - timedelta(minutes=59)).timestamp()))
        assert verify_telegram_login(data, BOT_TOKEN, now=now) == 42

    def test_no_hash_or_no_token_is_a_refusal_not_a_crash(self):
        data = signed()
        del data["hash"]
        assert verify_telegram_login(data, BOT_TOKEN) is None
        assert verify_telegram_login(signed(), "") is None


@pytest.fixture
def bot(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "telegram_bot_username", "career_helper_bot")
    return settings


@requires_db
class TestTelegramEndpoints:
    async def test_without_a_bot_the_button_is_off(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "telegram_bot_token", "")
        r = await client.get("/api/auth/telegram")
        assert r.json() == {"bot_username": None}
        assert (await client.post("/api/auth/telegram", json=signed())).status_code == 404

    async def test_config_names_the_bot_without_a_login(self, client, bot):
        r = await client.get("/api/auth/telegram")
        assert r.status_code == 200
        assert r.json() == {"bot_username": "career_helper_bot"}

    async def test_an_unlinked_telegram_does_not_create_an_account(self, client, bot):
        r = await client.post("/api/auth/telegram", json=signed())
        assert r.status_code == 401
        assert "не привязан" in r.json()["detail"]

    async def test_linked_telegram_logs_into_the_same_account(
        self, client, bot, user, auth_headers
    ):
        linked = await client.post("/api/auth/telegram/link", json=signed(), headers=auth_headers)
        assert linked.status_code == 200
        assert linked.json()["telegram_name"] == "@max"

        r = await client.post("/api/auth/telegram", json=signed())
        assert r.status_code == 200
        token = r.json()["access_token"]
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["email"] == user.email

    async def test_a_forged_answer_links_nothing(self, client, bot, auth_headers):
        data = signed()
        data["id"] = 7
        r = await client.post("/api/auth/telegram/link", json=data, headers=auth_headers)
        assert r.status_code == 401
        me = await client.get("/api/auth/me", headers=auth_headers)
        assert me.json()["telegram_name"] is None

    async def test_one_telegram_cannot_be_taken_from_another_account(
        self, client, bot, session, auth_headers
    ):
        from app.models import User

        session.add(User(email="other@example.com", password_hash="x", telegram_id=42))
        await session.commit()
        r = await client.post("/api/auth/telegram/link", json=signed(), headers=auth_headers)
        assert r.status_code == 409

    async def test_unlinking_stops_telegram_login(self, client, bot, auth_headers):
        await client.post("/api/auth/telegram/link", json=signed(), headers=auth_headers)
        r = await client.delete("/api/auth/telegram/link", headers=auth_headers)
        assert r.json()["telegram_name"] is None
        assert (await client.post("/api/auth/telegram", json=signed())).status_code == 401

    async def test_a_disabled_account_cannot_log_in_by_telegram(
        self, client, bot, session, user, auth_headers
    ):
        await client.post("/api/auth/telegram/link", json=signed(), headers=auth_headers)
        user.is_active = False
        await session.commit()
        assert (await client.post("/api/auth/telegram", json=signed())).status_code == 401
