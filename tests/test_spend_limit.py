"""The per-account spending ceiling.

The service is reachable from the internet while the owner's DeepSeek key pays
for every call, so this is the thing standing between a held F5 and a real
bill. It has to refuse BEFORE the model is called: a refusal afterwards means
the money is gone and the user is told they cannot have what they just paid for.

No LLM is called here — a test that got past the guard would fail on a missing
API key, which is itself a weak signal, so the tests assert the refusal
directly instead.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.models import User
from app.services.spend import ensure_can_spend, remaining_rub
from tests.dbmark import requires_db


@pytest.fixture
def limited(monkeypatch):
    """A 10 RUB ceiling, applied to the cached settings object."""
    settings = get_settings()
    monkeypatch.setattr(settings, "spend_limit_rub", 10.0)
    return settings


@pytest.fixture
def unlimited(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "spend_limit_rub", 0.0)
    return settings


def account(spent: str) -> User:
    return User(email="x@example.com", password_hash="x", spent_rub=Decimal(spent))


class TestRemaining:
    def test_no_limit_means_no_number(self, unlimited):
        """0 disables the check — the right setting for a laptop."""
        assert remaining_rub(account("5")) is None

    def test_what_is_left_counts_down(self, limited):
        assert remaining_rub(account("4")) == pytest.approx(6.0)

    def test_it_never_goes_negative(self, limited):
        """Overshooting by a fraction of a call must not read as a debt."""
        assert remaining_rub(account("12.5")) == 0.0


class TestTheGuard:
    def test_a_fresh_account_may_spend(self, limited):
        ensure_can_spend(account("0"))

    def test_an_account_under_the_ceiling_may_spend(self, limited):
        ensure_can_spend(account("9.99"))

    def test_an_exhausted_account_is_refused_with_402(self, limited):
        with pytest.raises(HTTPException) as e:
            ensure_can_spend(account("10"))
        assert e.value.status_code == 402

    def test_the_refusal_names_the_numbers(self, limited):
        """«Лимит исчерпан» without a number leaves the user guessing."""
        with pytest.raises(HTTPException) as e:
            ensure_can_spend(account("15"))
        detail = e.value.detail
        assert "10.00" in detail and "15.00" in detail

    def test_without_a_limit_nothing_is_refused(self, unlimited):
        ensure_can_spend(account("1000"))


@requires_db
class TestOverTheApi:
    async def test_the_remaining_budget_is_visible_before_it_runs_out(
        self, client, auth_headers, limited
    ):
        """Shown in /me so the ceiling is not a surprise 402 mid-click."""
        r = await client.get("/api/auth/me", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["remaining_rub"] == pytest.approx(10.0)

    async def test_a_paid_endpoint_refuses_when_the_budget_is_gone(
        self, client, session, user, auth_headers, limited
    ):
        """Refused before any model call, so the refusal itself costs nothing."""
        user.spent_rub = Decimal("10")
        await session.commit()

        r = await client.post(
            "/api/resumes",
            headers=auth_headers,
            files={"file": ("cv.txt", b"Python developer, 6 years", "text/plain")},
        )
        assert r.status_code == 402
        assert "лимит" in r.json()["detail"].lower()
