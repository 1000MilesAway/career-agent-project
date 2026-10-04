"""What a user may still spend on the model: a daily and a weekly quota.

The owner's DeepSeek key pays for every call, so every account has a ceiling.
Two windows, not one: the week is the real budget, and the day — about a third
of it — stops one evening of clicking from burning the whole week, and one
held F5 from burning anything much.

Units. A quota is kept in USD at the OFF-PEAK price (`Ledger.offpeak_usd`):
DeepSeek doubles its price during weekday peak hours, which in Moscow are the
very hours people look for work, and charging that to the user would make the
same button take 2% at 10:00 and 1% at 15:00. The user never sees dollars or
roubles — only the share of each window used, and when it comes back.

Periods are calendar ones in Moscow time: the day turns at 00:00, the week on
Monday at 00:00. A rolling window would be fairer by a few hours and cannot
answer "when do I get it back" in one phrase; this one can.

Checked BEFORE a paid step, never after: refusing afterwards means the money is
gone and the user is told they cannot have what they just paid for. A step that
started under the limit finishes, and its overshoot is carried into the next
period of the same window, so stopping right at 99% is not a free extra step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.llm import Ledger

from ..core.config import get_settings
from ..models import LlmCall, User

MSK = ZoneInfo("Europe/Moscow")

FREE = "free"
PAID = "paid"


def plan_of(user: User, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return PAID if user.paid_until is not None and user.paid_until > now else FREE


def _limits(plan: str) -> tuple[float, float]:
    s = get_settings()
    if plan == PAID:
        return s.paid_day_usd, s.paid_week_usd
    return s.free_day_usd, s.free_week_usd


def _periods(now: datetime) -> tuple[date, date]:
    """The Moscow day and the Monday of the Moscow week that `now` falls in."""
    today = now.astimezone(MSK).date()
    return today, today - timedelta(days=today.weekday())


@dataclass(frozen=True)
class Window:
    name: str  # "day" | "week"
    used_usd: float
    limit_usd: float  # <= 0: the window is off
    resets_at: datetime

    @property
    def on(self) -> bool:
        return self.limit_usd > 0

    @property
    def exhausted(self) -> bool:
        return self.on and self.used_usd >= self.limit_usd

    @property
    def used_pct(self) -> int:
        """Whole percent, capped at 100: an overshoot is not a debt to show."""
        if not self.on:
            return 0
        return min(100, int(self.used_usd / self.limit_usd * 100))


def _current(stored_period: date | None, stored_usd, period: date, step: timedelta,
             limit: float) -> float:
    """The counter as it stands in `period`, given what is stored.

    A counter from the same period is current. One from the period right
    before carries only its overshoot; anything older is fully reset.
    """
    used = float(stored_usd or 0)
    if stored_period == period:
        return used
    if stored_period == period - step and limit > 0:
        return max(0.0, used - limit)
    return 0.0


def windows(user: User, now: datetime | None = None) -> tuple[Window, Window]:
    now = now or datetime.now(UTC)
    day_limit, week_limit = _limits(plan_of(user, now))
    today, monday = _periods(now)
    day = Window(
        name="day",
        used_usd=_current(user.quota_day, user.quota_day_usd, today, timedelta(days=1),
                          day_limit),
        limit_usd=day_limit,
        resets_at=datetime.combine(today + timedelta(days=1), time(), MSK),
    )
    week = Window(
        name="week",
        used_usd=_current(user.quota_week, user.quota_week_usd, monday, timedelta(days=7),
                          week_limit),
        limit_usd=week_limit,
        resets_at=datetime.combine(monday + timedelta(days=7), time(), MSK),
    )
    return day, week


def exhausted_reason(user: User, now: datetime | None = None) -> str | None:
    """Why a paid step is refused right now, or None when it may run.

    The week is named first: when both are gone, "tomorrow" would be a lie.
    The admin is exempt — the owner testing their own service on the live
    server must not be locked out of it by their own quota.
    """
    if user.is_admin:
        return None
    day, week = windows(user, now)
    if week.exhausted:
        return "Недельный лимит исчерпан. Он обновится в понедельник в 00:00 по Москве."
    if day.exhausted:
        return "Дневной лимит исчерпан. Он обновится в 00:00 по Москве."
    return None


def ensure_can_spend(user: User) -> None:
    """Refuse a paid step when a quota window is used up.

    Raises before the model is called, so a refusal costs nothing. The message
    says when the quota comes back: "лимит исчерпан" alone reads like a bug.
    """
    reason = exhausted_reason(user)
    if reason is None:
        return
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=f"{reason} Уже купленные отчёты и материалы открываются бесплатно.",
    )


async def charge(session: AsyncSession, user: User, ledger: Ledger) -> float:
    """Count a run against the user's quota. Returns its real cost in RUB.

    The roubles are for the owner's accounting (`spent_rub`, `cost_rub`
    columns); the quota is charged the off-peak dollars.

    Does not commit: the cost belongs to the same transaction as whatever was
    bought, so a failed write cannot leave the user charged for a report that
    was never stored.
    """
    rub = round(ledger.rub, 4)
    usd = ledger.offpeak_usd
    journal(session, user, ledger)
    if rub:
        # Decimal, not float: the column is NUMERIC, and mixing the two makes
        # SQLAlchemy raise rather than silently round.
        user.spent_rub = Decimal(str(user.spent_rub)) + Decimal(str(rub))
    if usd:
        now = datetime.now(UTC)
        day, week = windows(user, now)
        today, monday = _periods(now)
        user.quota_day = today
        user.quota_day_usd = Decimal(str(round(day.used_usd + usd, 6)))
        user.quota_week = monday
        user.quota_week_usd = Decimal(str(round(week.used_usd + usd, 6)))
    return rub


def journal(session: AsyncSession, user: User, ledger: Ledger) -> None:
    """One `llm_calls` row per model call of the run, for the owner's dashboard.

    Here rather than at each call site: every paid step already comes through
    `charge`, and a step that forgot to journal would make the dashboard
    under-report exactly the cost nobody is looking for. Failed calls are
    journaled too — DeepSeek bills a timed-out answer all the same.
    """
    for c in ledger.calls:
        session.add(
            LlmCall(
                user_id=user.id,
                step=c.step[:32],
                model=c.model[:64],
                cache_hit=c.cache_hit,
                cache_miss=c.cache_miss,
                output=c.output,
                reasoning=c.reasoning,
                seconds=round(c.seconds, 3),
                usd=Decimal(str(round(c.usd, 8))),
                offpeak_usd=Decimal(str(round(c.usd / (c.peak or 1.0), 8))),
                rub=Decimal(str(round(c.rub, 6))),
                error=c.error,
            )
        )


def quota(user: User, now: datetime | None = None) -> dict:
    """The quota as the cabinet shows it: shares and reset times, no money."""
    now = now or datetime.now(UTC)
    plan = plan_of(user, now)
    unlimited = user.is_admin

    def shown(w: Window) -> dict | None:
        if unlimited or not w.on:
            return None
        return {"used_pct": w.used_pct, "resets_at": w.resets_at.isoformat()}

    day, week = windows(user, now)
    return {
        "plan": plan,
        "paid_until": user.paid_until.isoformat() if plan == PAID else None,
        "day": shown(day),
        "week": shown(week),
    }


def ledger_totals(ledger: Ledger) -> dict:
    """Tokens and money for one run, shaped for storing on an evaluation."""
    return {
        "model": ledger.calls[0].model if ledger.calls else "",
        "tokens_in": sum(c.total_in for c in ledger.calls),
        "tokens_out": sum(c.output for c in ledger.calls),
        "cost_rub": round(ledger.rub, 4),
    }
