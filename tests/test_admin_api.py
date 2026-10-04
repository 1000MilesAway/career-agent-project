"""The owner's dashboard: admin only, and its sums match the journal.

The journal is written by `spend.charge`, so the first test goes through it
rather than inserting rows: a step that is charged but not journaled is the
failure that would make the dashboard quietly under-report.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from app.models import LlmCall, User
from app.services.spend import MSK, charge
from core.llm import Ledger, Usage
from tests.dbmark import requires_db

pytestmark = requires_db


@pytest.fixture
async def admin_headers(client, session, user, auth_headers):
    user.is_admin = True
    await session.commit()
    return auth_headers


def ledger(*calls: Usage) -> Ledger:
    led = Ledger()
    for c in calls:
        led.add(c)
    return led


async def test_non_admin_is_refused(client, auth_headers):
    for path in ("/api/admin/users", "/api/admin/spend"):
        response = await client.get(path, headers=auth_headers)
        assert response.status_code == 403, path


async def test_anonymous_is_refused(client):
    assert (await client.get("/api/admin/users")).status_code == 401


async def test_charge_journals_every_call(session, user):
    peak = Usage(step="evaluate", model="deepseek-flash", cache_hit=100, cache_miss=900,
                 output=300, usd=0.002, peak=2.0)
    failed = Usage(step="evaluate", model="deepseek-flash", usd=0.0005, error="timeout")
    await charge(session, user, ledger(peak, failed))
    await session.commit()

    rows = (await session.execute(select(LlmCall).order_by(LlmCall.usd.desc()))).scalars().all()
    assert len(rows) == 2
    assert rows[0].user_id == user.id
    assert (rows[0].cache_hit, rows[0].cache_miss, rows[0].output) == (100, 900, 300)
    assert float(rows[0].offpeak_usd) == pytest.approx(0.001)
    assert rows[1].error == "timeout"


async def test_journal_outlives_the_account(session, user):
    """Erasing an account erases its data, not the owner's books."""
    await charge(session, user, ledger(Usage(step="chat", model="deepseek-flash", usd=0.01)))
    await session.commit()
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()

    row = (await session.execute(select(LlmCall))).scalar_one()
    assert row.user_id is None


async def test_users_lists_counts_and_quota(client, session, user, admin_headers):
    other = User(email="other_x@example.com", password_hash="x",
                 spent_rub=Decimal("12.5"), name="Мария")
    session.add(other)
    await session.commit()
    await charge(session, other, ledger(Usage(step="triage", model="deepseek-flash", usd=0.05)))
    await session.commit()

    body = (await client.get("/api/admin/users?sort=spent", headers=admin_headers)).json()
    assert body["total"] == 2
    top = body["items"][0]
    assert top["email"] == "other_x@example.com"
    assert top["plan"] == "free"
    assert top["has_resume"] is False
    assert top["spent_30d_rub"] > 0
    assert top["last_call_at"] is not None
    admin_row = body["items"][1]
    assert admin_row["plan"] == "admin"
    assert admin_row["day_pct"] is None


async def test_users_search_treats_underscore_literally(client, session, admin_headers):
    session.add(User(email="a_b@example.com", password_hash="x"))
    session.add(User(email="axb@example.com", password_hash="x"))
    await session.commit()

    body = (await client.get("/api/admin/users?q=a_b", headers=admin_headers)).json()
    assert [u["email"] for u in body["items"]] == ["a_b@example.com"]


async def test_spend_sums_by_day_step_and_user(client, session, user, admin_headers):
    await charge(session, user, ledger(
        Usage(step="evaluate", model="deepseek-flash", cache_miss=1000, output=200, usd=0.01),
        Usage(step="tailor_resume", model="deepseek-flash", output=500, usd=0.03, peak=2.0),
    ))
    # Outside a 7-day period: must not count.
    session.add(LlmCall(user_id=user.id, step="evaluate", model="deepseek-flash",
                        usd=Decimal("1"), offpeak_usd=Decimal("1"), rub=Decimal("80"),
                        created_at=datetime.now(UTC) - timedelta(days=20)))
    await session.commit()

    body = (await client.get("/api/admin/spend?days=7", headers=admin_headers)).json()
    assert body["totals"]["calls"] == 2
    assert body["totals"]["usd"] == pytest.approx(0.04)
    assert body["totals"]["offpeak_usd"] == pytest.approx(0.025)
    assert body["totals"]["cache_miss"] == 1000

    assert len(body["daily"]) == 7
    assert body["daily"][-1]["day"] == datetime.now(MSK).date().isoformat()
    assert sum(d["calls"] for d in body["daily"]) == 2

    assert [g["key"] for g in body["by_step"]] == ["tailor_resume", "evaluate"]
    assert body["top_users"][0]["label"] == user.email
    assert body["journal_since"] is not None

    total = await session.scalar(select(func.count()).select_from(LlmCall))
    assert total == 3
