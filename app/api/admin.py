"""The owner's dashboard: who uses the service, and what the model costs.

Admin only (`AdminDep`), and the one place in the API that answers in money:
users see shares of their quota and never roubles, the owner sees roubles and
dollars because paying for the key is their side of the business.

Read-only and free — nothing here calls the model. Every number is an
aggregate the database computes; the page never receives raw rows it would
have to sum itself, so a month of calls is one small payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from ..core.deps import AdminDep, SessionDep
from ..models import Evaluation, LlmCall, Resume, User, Vacancy
from ..services.spend import MSK, plan_of, windows

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _money(value: object) -> float:
    return round(float(value or 0), 4)


# ---------------------------------------------------------------- users


class AdminUser(BaseModel):
    id: uuid.UUID
    email: str | None
    name: str | None
    telegram_name: str | None
    is_admin: bool
    is_active: bool
    email_verified: bool
    plan: str
    paid_until: datetime | None
    created_at: datetime
    # Quota windows as the user sees them; null when the window is off.
    day_pct: int | None
    week_pct: int | None
    has_resume: bool
    vacancies: int
    evaluations: int
    # Lifetime, from `users.spent_rub` — it predates the call journal.
    spent_rub: float
    # From the journal: the last 30 days and the last call ever.
    spent_30d_rub: float
    last_call_at: datetime | None


class AdminUsers(BaseModel):
    total: int
    items: list[AdminUser]


UserSort = Literal["created", "spent", "last_call"]


@router.get("/users", response_model=AdminUsers)
async def users(
    session: SessionDep,
    _: AdminDep,
    q: str = Query("", max_length=100, description="почта, имя или Telegram"),
    sort: UserSort = "created",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> AdminUsers:
    """Accounts, newest first by default, with what each has done and cost.

    Counts come from grouped subqueries joined once, not from a query per row:
    a page of 50 is three aggregate scans whatever the page size.
    """
    month_ago = datetime.now(UTC) - timedelta(days=30)
    calls = (
        select(
            LlmCall.user_id.label("user_id"),
            func.max(LlmCall.created_at).label("last_call_at"),
            func.coalesce(
                func.sum(LlmCall.rub).filter(LlmCall.created_at >= month_ago), 0
            ).label("rub_30d"),
        )
        .where(LlmCall.user_id.is_not(None))
        .group_by(LlmCall.user_id)
        .subquery()
    )
    vacancies = (
        select(Vacancy.user_id.label("user_id"), func.count().label("n"))
        .group_by(Vacancy.user_id)
        .subquery()
    )
    evaluations = (
        select(Vacancy.user_id.label("user_id"), func.count(Evaluation.id).label("n"))
        .join(Evaluation, Evaluation.vacancy_id == Vacancy.id)
        .group_by(Vacancy.user_id)
        .subquery()
    )
    has_resume = (
        select(Resume.id)
        .where(Resume.user_id == User.id, Resume.is_active.is_(True))
        .exists()
    )

    filters: list[ColumnElement[bool]] = []
    needle = q.strip()
    if needle:
        # Escaped: a "_" in an address is a literal, not LIKE's wildcard.
        pattern = "%" + needle.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
        filters.append(
            or_(
                User.email.ilike(pattern, escape="\\"),
                User.name.ilike(pattern, escape="\\"),
                User.telegram_name.ilike(pattern, escape="\\"),
            )
        )

    order = {
        "created": (User.created_at.desc(),),
        "spent": (User.spent_rub.desc(), User.created_at.desc()),
        "last_call": (calls.c.last_call_at.desc().nulls_last(), User.created_at.desc()),
    }[sort]

    total = await session.scalar(select(func.count()).select_from(User).where(*filters))
    rows = await session.execute(
        select(
            User,
            has_resume.label("has_resume"),
            func.coalesce(vacancies.c.n, 0),
            func.coalesce(evaluations.c.n, 0),
            func.coalesce(calls.c.rub_30d, 0),
            calls.c.last_call_at,
        )
        .outerjoin(calls, calls.c.user_id == User.id)
        .outerjoin(vacancies, vacancies.c.user_id == User.id)
        .outerjoin(evaluations, evaluations.c.user_id == User.id)
        .where(*filters)
        .order_by(*order)
        .limit(limit)
        .offset(offset)
    )

    now = datetime.now(UTC)
    items = []
    for user, resume, n_vacancies, n_evaluations, rub_30d, last_call_at in rows.all():
        day, week = windows(user, now)
        plan = plan_of(user, now)
        items.append(
            AdminUser(
                id=user.id,
                email=user.email,
                name=user.name,
                telegram_name=user.telegram_name if user.telegram_id is not None else None,
                is_admin=user.is_admin,
                is_active=user.is_active,
                email_verified=user.email_verified_at is not None,
                plan="admin" if user.is_admin else plan,
                paid_until=user.paid_until if plan == "paid" else None,
                created_at=user.created_at,
                day_pct=None if user.is_admin or not day.on else day.used_pct,
                week_pct=None if user.is_admin or not week.on else week.used_pct,
                has_resume=bool(resume),
                vacancies=n_vacancies,
                evaluations=n_evaluations,
                spent_rub=_money(user.spent_rub),
                spent_30d_rub=_money(rub_30d),
                last_call_at=last_call_at,
            )
        )
    return AdminUsers(total=total or 0, items=items)


# ---------------------------------------------------------------- spend


class SpendTotals(BaseModel):
    calls: int
    errors: int
    cache_hit: int
    cache_miss: int
    output: int
    usd: float
    # What the same calls would cost off-peak; usd minus this is the peak
    # surcharge the owner carries and the quotas do not.
    offpeak_usd: float
    rub: float


class SpendDay(SpendTotals):
    day: date


class SpendGroup(SpendTotals):
    key: str


class SpendUser(SpendTotals):
    user_id: uuid.UUID | None
    # Email, name or Telegram; "удалённая учётка" once the account is gone.
    label: str


class Spend(BaseModel):
    days: int
    since: datetime
    totals: SpendTotals
    daily: list[SpendDay]
    by_step: list[SpendGroup]
    by_model: list[SpendGroup]
    top_users: list[SpendUser]
    # When the journal starts: spend before it exists only as the lifetime
    # sum below, so the page has to say where its charts begin.
    journal_since: datetime | None
    lifetime_rub: float


def _sums() -> list:
    return [
        func.count().label("calls"),
        func.count(LlmCall.error).label("errors"),
        func.coalesce(func.sum(LlmCall.cache_hit), 0).label("cache_hit"),
        func.coalesce(func.sum(LlmCall.cache_miss), 0).label("cache_miss"),
        func.coalesce(func.sum(LlmCall.output), 0).label("output"),
        func.coalesce(func.sum(LlmCall.usd), 0).label("usd"),
        func.coalesce(func.sum(LlmCall.offpeak_usd), 0).label("offpeak_usd"),
        func.coalesce(func.sum(LlmCall.rub), 0).label("rub"),
    ]


def _totals(row) -> dict:
    return {
        "calls": row.calls,
        "errors": row.errors,
        "cache_hit": int(row.cache_hit),
        "cache_miss": int(row.cache_miss),
        "output": int(row.output),
        "usd": round(float(row.usd), 6),
        "offpeak_usd": round(float(row.offpeak_usd), 6),
        "rub": _money(row.rub),
    }


TOP_USERS = 10


@router.get("/spend", response_model=Spend)
async def spend(
    session: SessionDep,
    _: AdminDep,
    days: int = Query(30, ge=1, le=366),
) -> Spend:
    """Model spend over the last `days` Moscow days, today included.

    Days are Moscow ones, as the quotas are: a call at 01:00 MSK belongs to
    the day the owner remembers it on, not to yesterday in UTC.
    """
    today = datetime.now(MSK).date()
    first = today - timedelta(days=days - 1)
    since = datetime.combine(first, time(), MSK)
    in_period = LlmCall.created_at >= since
    day = cast(func.timezone("Europe/Moscow", LlmCall.created_at), Date)

    totals = (await session.execute(select(*_sums()).where(in_period))).one()

    by_day = {
        row.day: row
        for row in await session.execute(
            select(day.label("day"), *_sums()).where(in_period).group_by(day)
        )
    }
    empty = {k: 0 for k in SpendTotals.model_fields}
    daily = [
        SpendDay(day=d, **(_totals(by_day[d]) if d in by_day else empty))
        for d in (first + timedelta(days=i) for i in range(days))
    ]

    async def grouped(column) -> list[SpendGroup]:
        rows = await session.execute(
            select(column.label("key"), *_sums())
            .where(in_period)
            .group_by(column)
            .order_by(func.sum(LlmCall.rub).desc())
        )
        return [SpendGroup(key=row.key, **_totals(row)) for row in rows]

    top = await session.execute(
        select(
            LlmCall.user_id,
            func.coalesce(User.email, User.name, User.telegram_name).label("label"),
            *_sums(),
        )
        .outerjoin(User, User.id == LlmCall.user_id)
        .where(in_period)
        .group_by(LlmCall.user_id, User.email, User.name, User.telegram_name)
        .order_by(func.sum(LlmCall.rub).desc())
        .limit(TOP_USERS)
    )
    top_users = [
        SpendUser(
            user_id=row.user_id,
            label=row.label or ("удалённая учётка" if row.user_id is None else "без имени"),
            **_totals(row),
        )
        for row in top
    ]

    return Spend(
        days=days,
        since=since,
        totals=SpendTotals(**_totals(totals)),
        daily=daily,
        by_step=await grouped(LlmCall.step),
        by_model=await grouped(LlmCall.model),
        top_users=top_users,
        journal_since=await session.scalar(select(func.min(LlmCall.created_at))),
        lifetime_rub=_money(await session.scalar(select(func.sum(User.spent_rub)))),
    )
