"""Buying Pro through Platega.

Platega is never called: `billing._request` is replaced by a fake that keeps
the requests and answers from a dict the test fills in.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Payment, User
from app.services import billing
from app.services.billing import (
    CANCELED,
    CHARGEBACKED,
    CONFIRMED,
    PENDING,
    _amount_matches,
    apply_status,
)
from tests.dbmark import requires_db

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
MERCHANT = "merchant-0000"
SECRET = "secret-0000"


def payment(status: str = PENDING, days: int = 30) -> Payment:
    return Payment(id=uuid.uuid4(), amount_rub=400, days=days, status=status)


def account(paid_until: datetime | None = None) -> User:
    return User(email="x@example.com", password_hash="x", paid_until=paid_until)


class TestApplyStatus:
    """No database: the transitions alone."""

    def test_a_confirmation_grants_the_days_from_now(self):
        user, pay = account(), payment()
        assert apply_status(user, pay, CONFIRMED, NOW)
        assert user.paid_until == NOW + timedelta(days=30)
        assert pay.paid_at == NOW

    def test_days_add_to_a_running_subscription(self):
        user = account(NOW + timedelta(days=5))
        apply_status(user, payment(), CONFIRMED, NOW)
        assert user.paid_until == NOW + timedelta(days=35)

    def test_a_lapsed_subscription_restarts_from_now(self):
        user = account(NOW - timedelta(days=5))
        apply_status(user, payment(), CONFIRMED, NOW)
        assert user.paid_until == NOW + timedelta(days=30)

    def test_a_repeated_confirmation_grants_nothing_more(self):
        """Platega retries callbacks, and the return from the pay page asks too."""
        user, pay = account(), payment()
        apply_status(user, pay, CONFIRMED, NOW)
        assert not apply_status(user, pay, CONFIRMED, NOW + timedelta(minutes=1))
        assert user.paid_until == NOW + timedelta(days=30)

    def test_confirmed_after_canceled_still_grants(self):
        user, pay = account(), payment(CANCELED)
        assert apply_status(user, pay, CONFIRMED, NOW)
        assert user.paid_until == NOW + timedelta(days=30)

    def test_a_chargeback_takes_the_days_back(self):
        user, pay = account(), payment()
        apply_status(user, pay, CONFIRMED, NOW)
        assert apply_status(user, pay, CHARGEBACKED, NOW + timedelta(days=1))
        assert pay.status == CHARGEBACKED
        assert user.paid_until == NOW + timedelta(days=1)  # not before "now"

    def test_a_chargeback_keeps_another_payments_days(self):
        user = account(NOW + timedelta(days=60))
        pay = payment(CONFIRMED)
        apply_status(user, pay, CHARGEBACKED, NOW)
        assert user.paid_until == NOW + timedelta(days=30)

    def test_a_confirmed_payment_is_not_walked_back_to_pending(self):
        user, pay = account(), payment(CONFIRMED)
        assert not apply_status(user, pay, PENDING, NOW)
        assert not apply_status(user, pay, CANCELED, NOW)
        assert pay.status == CONFIRMED

    def test_a_cancel_grants_nothing(self):
        user, pay = account(), payment()
        assert apply_status(user, pay, CANCELED, NOW)
        assert user.paid_until is None


class TestAmount:
    def test_the_same_sum_matches(self):
        assert _amount_matches({"paymentDetails": {"amount": 400, "currency": "RUB"}}, payment())

    @pytest.mark.parametrize(
        "details",
        [{"amount": 4, "currency": "RUB"}, {"amount": 400, "currency": "USD"}, {}, None],
    )
    def test_anything_else_does_not(self, details):
        assert not _amount_matches({"paymentDetails": details}, payment())


@pytest.fixture
def platega(monkeypatch):
    """Platega configured, and faked. `answers["status"]` is what GET returns."""
    s = get_settings()
    monkeypatch.setattr(s, "platega_merchant_id", MERCHANT)
    monkeypatch.setattr(s, "platega_secret", SECRET)
    state: dict = {"calls": [], "status": "PENDING", "amount": 400, "n": 0}

    async def fake(method, path, body=None):
        state["calls"].append((method, path, body))
        if method == "POST":
            state["n"] += 1
            tid = f"00000000-0000-0000-0000-{state['n']:012d}"
            return {"transactionId": tid, "status": "PENDING",
                    "url": f"https://pay.platega.io/?id={tid}", "expiresIn": "00:15:00"}
        return {"id": path.rsplit("/", 1)[1], "status": state["status"],
                "paymentDetails": {"amount": state["amount"], "currency": "RUB"}}

    monkeypatch.setattr(billing, "_request", fake)
    return state


CALLBACK_HEADERS = {"X-MerchantId": MERCHANT, "X-Secret": SECRET}


@requires_db
class TestApi:
    async def test_the_plan_and_its_price(self, client, auth_headers, platega):
        body = (await client.get("/api/billing", headers=auth_headers)).json()
        assert body == {"enabled": True, "price_rub": 400, "days": 30, "plan": "free",
                        "paid_until": None}

    async def test_without_keys_checkout_is_refused(self, client, auth_headers):
        assert (await client.get("/api/billing", headers=auth_headers)).json()["enabled"] is False
        response = await client.post("/api/billing/checkout", headers=auth_headers)
        assert response.status_code == 503

    async def test_checkout_records_the_payment_before_the_page(
        self, client, auth_headers, platega, session, user
    ):
        response = await client.post("/api/billing/checkout", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["url"].startswith("https://pay.platega.io/")

        _, path, body = platega["calls"][0]
        assert path == "/v2/transaction/process"
        assert body["paymentDetails"] == {"amount": 400, "currency": "RUB"}
        assert body["metadata"]["userId"] == str(user.id)
        assert body["return"].endswith("/search?payment=done")

        row = await session.scalar(select(Payment).where(Payment.user_id == user.id))
        assert row.status == PENDING and row.transaction_id and row.days == 30
        assert body["payload"] == str(row.id)

    async def test_a_second_click_reuses_the_page(self, client, auth_headers, platega):
        first = (await client.post("/api/billing/checkout", headers=auth_headers)).json()
        second = (await client.post("/api/billing/checkout", headers=auth_headers)).json()
        assert first == second
        assert platega["n"] == 1

    async def test_a_callback_without_our_secret_is_refused(
        self, client, auth_headers, platega
    ):
        await client.post("/api/billing/checkout", headers=auth_headers)
        platega["status"] = "CONFIRMED"
        for headers in ({}, {"X-MerchantId": MERCHANT, "X-Secret": "wrong"}):
            response = await client.post(
                "/api/billing/callback", headers=headers,
                json={"id": "00000000-0000-0000-0000-000000000001", "status": "CONFIRMED"},
            )
            assert response.status_code == 401
        me = (await client.get("/api/billing", headers=auth_headers)).json()
        assert me["plan"] == "free"

    async def test_a_callback_grants_once_by_platega_own_status(
        self, client, auth_headers, platega
    ):
        await client.post("/api/billing/checkout", headers=auth_headers)
        tid = "00000000-0000-0000-0000-000000000001"

        # The body says confirmed, Platega says pending: nothing is granted.
        response = await client.post(
            "/api/billing/callback", headers=CALLBACK_HEADERS,
            json={"id": tid, "status": "CONFIRMED", "amount": 400, "currency": "RUB"},
        )
        assert response.status_code == 200
        assert (await client.get("/api/billing", headers=auth_headers)).json()["plan"] == "free"

        platega["status"] = "CONFIRMED"
        for _ in range(3):
            await client.post("/api/billing/callback", headers=CALLBACK_HEADERS,
                              json={"id": tid, "status": "CONFIRMED"})
        me = (await client.get("/api/billing", headers=auth_headers)).json()
        assert me["plan"] == "paid"
        until = datetime.fromisoformat(me["paid_until"])
        assert timedelta(days=29) < until - datetime.now(UTC) <= timedelta(days=30)

    async def test_a_different_amount_grants_nothing(self, client, auth_headers, platega):
        await client.post("/api/billing/checkout", headers=auth_headers)
        platega.update(status="CONFIRMED", amount=4)
        await client.post("/api/billing/callback", headers=CALLBACK_HEADERS,
                          json={"id": "00000000-0000-0000-0000-000000000001"})
        assert (await client.get("/api/billing", headers=auth_headers)).json()["plan"] == "free"

    async def test_an_unknown_transaction_is_answered_and_ignored(self, client, platega):
        response = await client.post("/api/billing/callback", headers=CALLBACK_HEADERS,
                                     json={"id": str(uuid.uuid4()), "status": "CONFIRMED"})
        assert response.status_code == 200

    async def test_the_return_from_the_pay_page_settles_without_a_callback(
        self, client, auth_headers, platega
    ):
        await client.post("/api/billing/checkout", headers=auth_headers)
        platega["status"] = "CONFIRMED"
        body = (await client.post("/api/billing/sync", headers=auth_headers)).json()
        assert body["plan"] == "paid"
