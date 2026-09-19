"""What a user has spent on the model, and whether they may spend more.

The limit is a per-account budget in roubles, set by SPEND_LIMIT_RUB. It exists
because the service is reachable from the internet while the owner's DeepSeek
key pays for every call: without a ceiling, one tester holding F5 spends real
money, and nothing in the pipeline would stop them.

It is checked BEFORE a paid step, never after. Refusing afterwards would mean
the money is already gone and the user is told they cannot have what they just
paid for.

SPEND_LIMIT_RUB=0 disables the check, which is the right setting for a laptop
and the wrong one for a public host.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.llm import Ledger

from ..core.config import get_settings
from ..models import User


def remaining_rub(user: User) -> float | None:
    """What is left of this account's budget, or None when no limit is set."""
    limit = get_settings().spend_limit_rub
    if limit <= 0:
        return None
    return max(0.0, limit - float(user.spent_rub))


def ensure_can_spend(user: User) -> None:
    """Refuse a paid step when the budget is gone.

    Raises before the model is called, so a refusal costs nothing. The message
    names the number, because "лимит исчерпан" without one leaves the user
    guessing whether they hit a bug.
    """
    left = remaining_rub(user)
    if left is None or left > 0:
        return

    limit = get_settings().spend_limit_rub
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=(
            f"Исчерпан лимит расхода: {limit:.2f} ₽ на аккаунт. "
            f"Потрачено {float(user.spent_rub):.2f} ₽. "
            "Уже купленные отчёты и материалы открываются бесплатно."
        ),
    )


async def charge(session: AsyncSession, user: User, ledger: Ledger) -> float:
    """Add a run's cost to the user's total. Returns what this run cost, in RUB.

    Does not commit: the cost belongs to the same transaction as whatever was
    bought, so a failed write cannot leave the user charged for a report that
    was never stored.
    """
    rub = round(ledger.rub, 4)
    if rub:
        # Decimal, not float: the column is NUMERIC, and mixing the two makes
        # SQLAlchemy raise rather than silently round.
        user.spent_rub = Decimal(str(user.spent_rub)) + Decimal(str(rub))
    return rub


def ledger_totals(ledger: Ledger) -> dict:
    """Tokens and money for one run, shaped for storing on an evaluation."""
    return {
        "model": ledger.calls[0].model if ledger.calls else "",
        "tokens_in": sum(c.total_in for c in ledger.calls),
        "tokens_out": sum(c.output for c in ledger.calls),
        "cost_rub": round(ledger.rub, 4),
    }
