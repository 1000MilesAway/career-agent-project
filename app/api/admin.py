"""The owner's dashboard: who uses the service, what the model costs, and
which sources users get vacancies from.

Admin only (`AdminDep`), and the one place in the API that answers in money:
users see shares of their quota and never roubles, the owner sees roubles and
dollars because paying for the key is their side of the business.

Free — nothing here calls the model. Read-only except the source switches
at the end, which change what users see and nothing they own. Every number is an
aggregate the database computes; the page never receives raw rows it would
have to sum itself, so a month of calls is one small payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import Date, and_, cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.elements import ColumnElement

from core.sources import employers, tg_channels

from ..core.deps import AdminDep, SessionDep
from ..models import (
    Application,
    Artifact,
    CompanyName,
    Evaluation,
    Job,
    LlmCall,
    Posting,
    Resume,
    SourceFetch,
    SourceSetting,
    User,
    Vacancy,
)
from ..services import sources as switches
from ..services.scan import MATCH_WINDOW
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


# ---------------------------------------------------------------- funnel

# The path a new account is meant to walk, in order. Each stage counts the
# accounts that reached it AND every stage before it, so the bars only ever
# shrink and "lost between two stages" is a subtraction. Most of these are
# prerequisites in the product anyway: no scan without a CV, no evaluation
# without a scanned vacancy.
FUNNEL = ("signed_up", "can_log_in", "resume", "scan", "evaluation", "applied")

# Done by some and skipped by others without being stuck: counted on their
# own, as a share of the cohort, outside the chained funnel.
EXTRAS = ("triage", "saved", "artifact", "paid")

# Tracker statuses that mean the user sent an application. "discarded" is a
# vacancy put aside before applying, so it is not one.
APPLIED = ("applied", "responded", "interview", "tech_interview", "offer", "rejected")

Channel = Literal["all", "email", "telegram"]


class FunnelCohort(BaseModel):
    # The Monday of the Moscow week the accounts signed up in.
    week: date
    # Chained counts, one per FUNNEL stage.
    stages: list[int]


class Funnel(BaseModel):
    days: int  # 0: every account ever
    since: datetime | None
    stages: list[str]
    counts: list[int]
    extras: dict[str, int]
    cohorts: list[FunnelCohort]


def _reached() -> dict[str, ColumnElement[bool]]:
    """Per account, whether it got to each stage — correlated EXISTS, so the
    whole funnel is one pass over the cohort's users."""
    vacancy_of_user = Vacancy.user_id == User.id
    return {
        "signed_up": User.id.is_not(None),
        # A self-signup cannot log in until the address is confirmed;
        # a Telegram account is in from its first login.
        "can_log_in": or_(User.email_verified_at.is_not(None), User.telegram_id.is_not(None)),
        "resume": select(Resume.id).where(Resume.user_id == User.id).exists(),
        "scan": select(Job.id).where(Job.user_id == User.id, Job.kind == "scan").exists(),
        "evaluation": select(Evaluation.id)
        .join(Vacancy, Evaluation.vacancy_id == Vacancy.id)
        .where(vacancy_of_user)
        .exists(),
        "applied": select(Application.id)
        .join(Vacancy, Application.vacancy_id == Vacancy.id)
        .where(vacancy_of_user, Application.status.in_(APPLIED))
        .exists(),
        "triage": select(Job.id)
        .where(Job.user_id == User.id, Job.kind == "triage", Job.status == "done")
        .exists(),
        "saved": select(Vacancy.id)
        .where(vacancy_of_user, Vacancy.saved_at.is_not(None))
        .exists(),
        "artifact": select(Artifact.id)
        .join(Evaluation, Artifact.evaluation_id == Evaluation.id)
        .join(Vacancy, Evaluation.vacancy_id == Vacancy.id)
        .where(vacancy_of_user)
        .exists(),
        # Ever given a subscription, lapsed or not: the funnel asks who
        # converted, not who is paying today.
        "paid": User.paid_until.is_not(None),
    }


@router.get("/funnel", response_model=Funnel)
async def funnel(
    session: SessionDep,
    _: AdminDep,
    days: int = Query(30, ge=0, le=366, description="0 — все учётки"),
    channel: Channel = "all",
) -> Funnel:
    """How far the accounts that signed up in the period got.

    A cohort funnel: the period picks WHO (signed up in the last `days` Moscow
    days), and every stage counts what those accounts ever did. Counting the
    period's events instead would mix last month's sign-ups into this month's
    evaluations and show conversions above 100%.

    Admins are left out: the owner testing the service is not a user.
    """
    since = None
    filters: list[ColumnElement[bool]] = [User.is_admin.is_(False)]
    if days:
        first = datetime.now(MSK).date() - timedelta(days=days - 1)
        since = datetime.combine(first, time(), MSK)
        filters.append(User.created_at >= since)
    if channel == "email":
        filters.append(User.email.is_not(None))
    elif channel == "telegram":
        filters.append(User.email.is_(None))

    reached = _reached()
    flags = select(
        User.id,
        cast(
            func.date_trunc("week", func.timezone("Europe/Moscow", User.created_at)), Date
        ).label("week"),
        *(cond.label(name) for name, cond in reached.items()),
    ).where(*filters).subquery()

    chained = []
    for i in range(len(FUNNEL)):
        chained.append(func.count().filter(and_(*(flags.c[s] for s in FUNNEL[: i + 1]))))
    extras = [func.count().filter(flags.c[name]) for name in EXTRAS]

    total = (await session.execute(select(*chained, *extras).select_from(flags))).one()
    by_week = await session.execute(
        select(flags.c.week, *chained).group_by(flags.c.week).order_by(flags.c.week.desc())
    )

    return Funnel(
        days=days,
        since=since,
        stages=list(FUNNEL),
        counts=list(total[: len(FUNNEL)]),
        extras=dict(zip(EXTRAS, total[len(FUNNEL):], strict=True)),
        cohorts=[FunnelCohort(week=row[0], stages=list(row[1:])) for row in by_week],
    )


# ---------------------------------------------------------------- sources

# Searched by query, so they have no part to switch off: listed for their
# numbers only. Russian: the page shows them.
AGGREGATORS = {"habr": "Хабр Карьера", "hh": "hh.ru", "geekjob": "geekjob"}

# Telegram groups in the order the page lists them; `special` is in the file
# but no scan reads it, which is worth seeing next to the channels that are.
CHANNEL_GROUPS = ("core", "adjacent", "special")


class SourceStats(BaseModel):
    postings: int = 0
    # Open and seen within the match window: what a scan can still hand out.
    active: int = 0
    last_seen_at: datetime | None = None


class Aggregator(SourceStats):
    source: str
    label: str
    # When any of its listings was last read.
    fetched_at: datetime | None = None


class CompanyAlias(SourceStats):
    # As the source wrote it, and what users see instead; null — unrenamed.
    name: str
    display_name: str | None = None


class ChannelItem(CompanyAlias):
    key: str
    # The list-file group, or null for a channel only a named scan read.
    group: str | None
    hidden: bool


class BoardItem(SourceStats):
    key: str
    label: str
    group: str
    hidden: bool
    # The company names its postings carry: one for most boards, a dozen
    # legal entities for Sber.
    names: list[CompanyAlias]


class Sources(BaseModel):
    aggregators: list[Aggregator]
    channels: list[ChannelItem]
    boards: list[BoardItem]


def _stats() -> list:
    active = and_(
        Posting.closed_at.is_(None),
        Posting.last_seen_at >= datetime.now(UTC) - MATCH_WINDOW,
    )
    return [
        func.count().label("postings"),
        func.count().filter(active).label("active"),
        func.max(Posting.last_seen_at).label("last_seen_at"),
    ]


def _add(into: SourceStats, row) -> None:
    into.postings += row.postings
    into.active += row.active
    if row.last_seen_at and (into.last_seen_at is None or row.last_seen_at > into.last_seen_at):
        into.last_seen_at = row.last_seen_at


@router.get("/sources", response_model=Sources)
async def sources(session: SessionDep, _: AdminDep) -> Sources:
    """Aggregators, Telegram channels and employer boards, with pool numbers.

    Channels and boards come from the list files, so one nobody has read yet
    is listed with zeros; a channel that is only in the pool (read by a named
    scan) is listed too, or it could not be hidden.
    """
    state = await switches.load(session)

    fetched = dict(
        (await session.execute(
            select(SourceFetch.source, func.max(SourceFetch.fetched_at))
            .group_by(SourceFetch.source)
        )).all()
    )
    by_source = {
        row.source: row
        for row in await session.execute(
            select(Posting.source, *_stats())
            .where(Posting.source.in_(AGGREGATORS))
            .group_by(Posting.source)
        )
    }
    aggregators = []
    for source, label in AGGREGATORS.items():
        item = Aggregator(source=source, label=label, fetched_at=fetched.get(source))
        if source in by_source:
            _add(item, by_source[source])
        aggregators.append(item)

    channels: dict[str, ChannelItem] = {}
    for group in CHANNEL_GROUPS:
        for name in tg_channels.channel_names(groups=(group,)):
            channels.setdefault(name, ChannelItem(
                key=name, name=f"@{name}", group=group, hidden=False,
            ))
    rows = await session.execute(
        select(Posting.company, *_stats())
        .where(Posting.source == "telegram")
        .group_by(Posting.company)
    )
    for row in rows:
        key = switches.channel_of(row.company)
        if not key:
            continue
        item = channels.setdefault(
            key, ChannelItem(key=key, name=f"@{key}", group=None, hidden=False)
        )
        _add(item, row)
    for item in channels.values():
        item.hidden = item.key in state.channels()
        item.display_name = state.names.get(item.name)

    boards = {
        b.key: BoardItem(key=b.key, label=b.name, group=b.group, hidden=False, names=[])
        for b in employers.load()
    }
    board = func.concat(
        func.split_part(Posting.external_id, "/", 1),
        "/",
        func.split_part(Posting.external_id, "/", 2),
    )
    rows = await session.execute(
        select(board.label("board"), Posting.company, *_stats())
        .where(Posting.source == "company")
        .group_by(board, Posting.company)
        .order_by(func.count().desc())
    )
    for row in rows:
        # A board taken out of the file still has postings in the pool; it is
        # listed so its switch keeps working while they age out.
        item = boards.setdefault(
            row.board, BoardItem(key=row.board, label=row.board, group="", hidden=False, names=[])
        )
        _add(item, row)
        if row.company:
            alias = CompanyAlias(name=row.company, display_name=state.names.get(row.company))
            _add(alias, row)
            item.names.append(alias)
    for item in boards.values():
        item.hidden = item.key in state.boards()

    return Sources(
        aggregators=aggregators,
        channels=list(channels.values()),
        boards=list(boards.values()),
    )


class SourceSwitch(BaseModel):
    source: Literal["telegram", "company"]
    key: str = Field(min_length=1, max_length=255)
    hidden: bool


@router.put("/sources/hidden", response_model=SourceSwitch)
async def set_hidden(body: SourceSwitch, session: SessionDep, _: AdminDep) -> SourceSwitch:
    """Hide a channel or board from every user, or bring it back.

    Takes effect on the next read of the results: hidden rows leave them,
    except those a user saved, tracked or bought a report for. Nothing is
    deleted, so showing it again restores the rows as they were.
    """
    key = body.key.strip().removeprefix("@") if body.source == "telegram" else body.key.strip()
    await session.execute(
        insert(SourceSetting)
        .values(source=body.source, key=key, hidden=body.hidden)
        .on_conflict_do_update(
            index_elements=["source", "key"],
            set_={"hidden": body.hidden, "updated_at": func.now()},
        )
    )
    await session.commit()
    return SourceSwitch(source=body.source, key=key, hidden=body.hidden)


class CompanyRename(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Empty or null — show the name as the source wrote it.
    display_name: str | None = Field(default=None, max_length=255)


@router.put("/sources/name", response_model=CompanyRename)
async def set_name(body: CompanyRename, session: SessionDep, _: AdminDep) -> CompanyRename:
    """Show a company under another name, wherever users see it."""
    shown = (body.display_name or "").strip()
    if not shown or shown == body.name:
        await session.execute(delete(CompanyName).where(CompanyName.name == body.name))
        await session.commit()
        return CompanyRename(name=body.name, display_name=None)
    await session.execute(
        insert(CompanyName)
        .values(name=body.name, display_name=shown)
        .on_conflict_do_update(
            index_elements=["name"], set_={"display_name": shown, "updated_at": func.now()}
        )
    )
    await session.commit()
    return CompanyRename(name=body.name, display_name=shown)
