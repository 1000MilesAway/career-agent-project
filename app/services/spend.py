"""What a user has spent on the model.

No limit is enforced: the user chose to open the service to testers without
one. This exists anyway for two reasons — the number has to be accumulating
before a limit can ever be switched on retroactively, and while testing it
answers "what did this account cost me".

Turning a limit on later means adding one check in front of the paid steps;
the data it would read is already here.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.llm import Ledger

from ..models import User


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
