"""The per-account quota: a daily and a weekly window, per plan.

The service is reachable from the internet while the owner's DeepSeek key pays
for every call, so this is the thing standing between a held F5 and a real
bill. It has to refuse BEFORE the model is called: a refusal afterwards means
the money is gone and the user is told they cannot have what they just paid for.

No LLM is called here — a test that got past the guard would fail on a missing
API key, which is itself a weak signal, so the tests assert the refusal
directly instead.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.core.config import get_settings
from app.models import User
from app.services import spend
from app.services.spend import ensure_can_spend, exhausted_reason, quota, windows
from core.llm import Ledger, Usage
from tests.dbmark import requires_db

# Wednesday 2026-09-30, 12:00 Moscow time.
WED = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
TODAY = date(2026, 9, 30)
MONDAY = date(2026, 9, 28)


@pytest.fixture
def limits(monkeypatch):
    """Round numbers, so the shares read at a glance."""
    s = get_settings()
    monkeypatch.setattr(s, "free_day_usd", 1.0)
    monkeypatch.setattr(s, "free_week_usd", 3.0)
    monkeypatch.setattr(s, "paid_day_usd", 10.0)
    monkeypatch.setattr(s, "paid_week_usd", 30.0)
    return s


@pytest.fixture
def frozen(monkeypatch):
    """Pin "now" to WED for code that does not take it as an argument."""
    real = spend.windows
    monkeypatch.setattr(spend, "windows", lambda user, now=None: real(user, WED))
    monkeypatch.setattr(spend, "_periods", lambda now: (TODAY, MONDAY))


def account(day: str = "0", week: str = "0", *, day_of=TODAY, week_of=MONDAY,
            paid_until=None) -> User:
    return User(
        email="x@example.com", password_hash="x", is_admin=False, spent_rub=Decimal("0"),
        quota_day=day_of, quota_day_usd=Decimal(day),
        quota_week=week_of, quota_week_usd=Decimal(week),
        paid_until=paid_until,
    )


class TestWindows:
    def test_shares_count_up(self, limits):
        day, week = windows(account("0.5", "0.75"), WED)
        assert day.used_pct == 50
        assert week.used_pct == 25

    def test_an_overshoot_reads_as_full_not_as_debt(self, limits):
        day, _ = windows(account("1.4", "1.4"), WED)
        assert day.used_pct == 100

    def test_the_day_turns_at_moscow_midnight(self, limits):
        """21:30 UTC is already tomorrow in Moscow: yesterday's spend is gone."""
        late = datetime(2026, 9, 30, 21, 30, tzinfo=UTC)
        day, week = windows(account("0.5", "0.5"), late)
        assert day.used_usd == 0
        assert week.used_usd == 0.5  # same week
        assert day.resets_at.isoformat() == "2026-10-02T00:00:00+03:00"

    def test_the_week_turns_on_monday(self, limits):
        _, week = windows(account(week="2", week_of=MONDAY - timedelta(days=14)), WED)
        assert week.used_usd == 0
        assert week.resets_at.isoformat() == "2026-10-05T00:00:00+03:00"

    def test_an_overshoot_carries_into_the_next_period(self, limits):
        """Stopping at 99% must not buy a free extra step."""
        day, _ = windows(account("1.3", day_of=TODAY - timedelta(days=1)), WED)
        assert day.used_usd == pytest.approx(0.3)

    def test_only_the_period_right_before_carries(self, limits):
        day, _ = windows(account("1.3", day_of=TODAY - timedelta(days=2)), WED)
        assert day.used_usd == 0

    def test_a_paid_plan_has_the_paid_limits(self, limits):
        paid = account("5", paid_until=WED + timedelta(days=3))
        day, _ = windows(paid, WED)
        assert day.used_pct == 50

    def test_a_lapsed_subscription_falls_back_to_free(self, limits):
        lapsed = account(paid_until=WED - timedelta(seconds=1))
        assert spend.plan_of(lapsed, WED) == "free"


class TestTheGuard:
    def test_a_fresh_account_may_spend(self, limits):
        assert exhausted_reason(account(), WED) is None

    def test_a_full_day_is_refused_and_says_when_it_returns(self, limits):
        reason = exhausted_reason(account("1", "1"), WED)
        assert reason and "Дневной" in reason and "00:00" in reason

    def test_the_week_is_named_when_both_are_gone(self, limits):
        """Saying "tomorrow" when the week is gone would be a lie."""
        reason = exhausted_reason(account("1", "3"), WED)
        assert reason and "Недельный" in reason and "понедельник" in reason

    def test_a_window_set_to_zero_is_off(self, limits, monkeypatch):
        monkeypatch.setattr(limits, "free_day_usd", 0.0)
        monkeypatch.setattr(limits, "free_week_usd", 0.0)
        assert exhausted_reason(account("100", "100"), WED) is None

    def test_the_admin_is_never_locked_out(self, limits):
        admin = account("100", "100")
        admin.is_admin = True
        assert exhausted_reason(admin, WED) is None

    def test_the_refusal_is_a_402_with_no_money_in_it(self, limits, frozen):
        with pytest.raises(HTTPException) as e:
            ensure_can_spend(account("1", "1"))
        assert e.value.status_code == 402
        assert "₽" not in e.value.detail and "$" not in e.value.detail


class _Journal:
    """A session stand-in: `charge` only adds journal rows to it."""

    def __init__(self) -> None:
        self.rows: list = []

    def add(self, row) -> None:
        self.rows.append(row)


class TestCharging:
    async def test_the_quota_is_charged_the_off_peak_price(self, limits, frozen):
        """The peak surcharge is the owner's cost: the same click, the same share."""
        ledger = Ledger([Usage(step="evaluate", model="deepseek-flash", usd=0.4, peak=2.0)])
        user = account()

        journal = _Journal()
        await spend.charge(journal, user, ledger)
        assert float(user.quota_day_usd) == pytest.approx(0.2)
        assert float(user.quota_week_usd) == pytest.approx(0.2)
        # The owner's books keep the real price.
        assert float(user.spent_rub) == pytest.approx(round(ledger.rub, 4))
        # One journal row per call, with both prices.
        (row,) = journal.rows
        assert float(row.usd) == pytest.approx(0.4)
        assert float(row.offpeak_usd) == pytest.approx(0.2)

    async def test_a_new_day_starts_from_the_carried_overshoot(self, limits, frozen):
        ledger = Ledger([Usage(step="evaluate", model="deepseek-flash", usd=0.1)])
        user = account("1.2", day_of=TODAY - timedelta(days=1))

        await spend.charge(_Journal(), user, ledger)
        assert user.quota_day == TODAY
        assert float(user.quota_day_usd) == pytest.approx(0.3)

    def test_the_payload_has_shares_and_no_money(self, limits):
        shown = quota(account("0.5", "1.5"), WED)
        assert shown["plan"] == "free"
        assert shown["day"]["used_pct"] == 50
        assert shown["week"]["used_pct"] == 50
        assert "usd" not in str(shown) and "rub" not in str(shown)


@requires_db
class TestOverTheApi:
    async def test_a_paid_endpoint_refuses_when_the_quota_is_gone(
        self, client, session, user, auth_headers, limits
    ):
        """Refused before any model call, so the refusal itself costs nothing."""
        today, monday = spend._periods(datetime.now(UTC))
        user.quota_day, user.quota_day_usd = today, Decimal("1")
        user.quota_week, user.quota_week_usd = monday, Decimal("1")
        await session.commit()

        r = await client.post(
            "/api/resumes",
            headers=auth_headers,
            files={"file": ("cv.txt", b"Python developer, 6 years", "text/plain")},
        )
        assert r.status_code == 402
        assert "лимит" in r.json()["detail"].lower()

    async def test_me_names_the_plan(self, client, auth_headers):
        r = await client.get("/api/auth/me", headers=auth_headers)
        assert r.json()["plan"] == "free"
        assert not any("rub" in key for key in r.json())


@requires_db
class TestTheScanPathRespectsTheQuota:
    """Deriving a search profile is a paid call, and it sits on the scan path.

    Every other paid step checks the quota at its own entry point; this one
    was reached from POST /api/vacancies/scan, which has no check of its own,
    so an account out of quota could still buy a profile.
    """

    async def test_a_scan_that_would_derive_a_profile_is_refused(
        self, client, session, user, auth_headers, limits, monkeypatch
    ):
        from app.models import Resume
        from app.services import scan

        today, monday = spend._periods(datetime.now(UTC))
        user.quota_day, user.quota_day_usd = today, Decimal("1")
        user.quota_week, user.quota_week_usd = monday, Decimal("1")
        resume = Resume(
            user_id=user.id,
            filename="cv.md",
            raw_text="Python developer",
            profile={"skills": ["Python"]},
            search_profile=None,
            is_active=True,
        )
        session.add(resume)
        await session.commit()

        def must_not_run(llm, profile):  # pragma: no cover - the point of the test
            raise AssertionError("the model was called despite an exhausted quota")

        monkeypatch.setattr(scan, "build_search_profile", must_not_run)

        r = await client.post(
            "/api/vacancies/scan", headers=auth_headers, json={"source": "habr"}
        )
        assert r.status_code == 402
        assert "лимит" in r.json()["detail"].lower()
