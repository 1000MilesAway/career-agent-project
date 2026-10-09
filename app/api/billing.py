"""The plan and its payment: what Pro costs, a pay page, and Platega's callback."""

from __future__ import annotations

import hmac
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.deps import SessionDep, UserDep
from ..services import billing
from ..services.spend import PAID, plan_of

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


class Billing(BaseModel):
    # False until Platega's keys are on the server: the plans are shown,
    # the button is not.
    enabled: bool
    price_rub: int
    days: int
    # "free" or "paid", as in /api/auth/me.
    plan: str
    paid_until: datetime | None


class Checkout(BaseModel):
    url: str


def _billing(user) -> Billing:
    s = get_settings()
    plan = plan_of(user)
    return Billing(
        enabled=billing.billing_enabled(),
        price_rub=s.pro_price_rub,
        days=s.pro_days,
        plan=plan,
        paid_until=user.paid_until if plan == PAID else None,
    )


@router.get("")
async def read(user: UserDep) -> Billing:
    return _billing(user)


@router.post("/checkout")
async def checkout(user: UserDep, session: SessionDep) -> Checkout:
    """A pay page for one month of Pro. The browser goes there itself."""
    try:
        url = await billing.checkout(session, user)
    except billing.BillingUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Оплата сейчас недоступна, попробуйте позже",
        ) from exc
    return Checkout(url=url)


@router.post("/sync")
async def sync(user: UserDep, session: SessionDep) -> Billing:
    """Called on the return from the pay page: the callback may not be here yet."""
    await billing.settle_recent(session, user)
    await session.refresh(user)
    return _billing(user)


def _same(given: str | None, expected: str) -> bool:
    return bool(expected) and given is not None and hmac.compare_digest(given, expected)


@router.post("/callback", include_in_schema=False)
async def callback(
    request: Request,
    session: SessionDep,
    merchant: Annotated[str | None, Header(alias="X-MerchantId")] = None,
    secret: Annotated[str | None, Header(alias="X-Secret")] = None,
) -> dict[str, bool]:
    """Platega says a transaction changed. Its word is checked, not taken.

    The headers must carry our own MerchantId and secret; even then only the
    transaction id is read from the body, and its status is asked of Platega
    (`billing.settle`). A 5xx makes Platega retry, which is wanted when that
    ask failed; an unknown transaction is answered 200, since a retry cannot
    make it known.
    """
    s = get_settings()
    if not (_same(merchant, s.platega_merchant_id) and _same(secret, s.platega_secret)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="not JSON") from exc
    transaction_id = body.get("id") if isinstance(body, dict) else None
    if not transaction_id:
        raise HTTPException(status_code=400, detail="no transaction id")
    try:
        await billing.settle_transaction(session, str(transaction_id))
    except billing.BillingUnavailable as exc:
        log.warning("callback for %s not settled: %s", transaction_id, exc)
        raise HTTPException(status_code=503, detail="try again") from exc
    return {"ok": True}
