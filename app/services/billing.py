"""Buying Pro through Platega (https://docs.platega.io).

One payment buys `pro_days` of the paid plan; there is no recurring charge,
so nothing has to be cancelled and no card is kept. The plan itself is still
`users.paid_until` (services/spend.py): a payment only moves that date.

What decides that money arrived is Platega's own answer to "what is the status
of transaction X", never the body of a request that reached us. A callback is
only a cue to ask: its headers carry our secret, but a leaked secret should not
be enough to mint subscriptions, and asking costs one GET. The same ask runs
when the user comes back from the pay page, so a callback that never arrives
(Platega gives up after three tries) does not leave a paid user on the free plan.

httpx rather than an SDK, as with mail: two calls do not justify a dependency.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..models import Payment, User

log = logging.getLogger(__name__)

PENDING = "pending"
CONFIRMED = "confirmed"
CANCELED = "canceled"
CHARGEBACKED = "chargebacked"

# Platega's statuses, as its API spells them, to ours.
STATUSES = {
    "PENDING": PENDING,
    "CONFIRMED": CONFIRMED,
    "CANCELED": CANCELED,
    "CHARGEBACKED": CHARGEBACKED,
}

# A pay page lives 15 minutes (`expiresIn` in Platega's answer). A second
# click inside that reuses it rather than opening another transaction, with a
# margin so the user is not sent to a page about to expire.
REUSE_PAGE = timedelta(minutes=10)
# How far back a return from the pay page looks for an unsettled payment.
SYNC_WINDOW = timedelta(days=2)


class BillingUnavailable(Exception):
    """Platega refused, timed out, or is not configured."""


def billing_enabled() -> bool:
    s = get_settings()
    return bool(s.platega_merchant_id and s.platega_secret)


def _headers() -> dict[str, str]:
    s = get_settings()
    return {"X-MerchantId": s.platega_merchant_id, "X-Secret": s.platega_secret}


async def _request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """One call to Platega's API; the only place the network is touched."""
    if not billing_enabled():
        raise BillingUnavailable("platega is not configured")
    url = get_settings().platega_url.rstrip("/") + path
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(method, url, headers=_headers(), json=body)
    except httpx.HTTPError as exc:
        raise BillingUnavailable(str(exc)) from exc
    if response.status_code >= 400:
        log.warning("platega refused %s %s: %s %s", method, path, response.status_code,
                    response.text[:500])
        raise BillingUnavailable(f"platega answered {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise BillingUnavailable("platega answered not JSON") from exc


def _who(user: User) -> str:
    return user.email or (f"@{user.telegram_name}" if user.telegram_name else str(user.id))


async def checkout(session: AsyncSession, user: User, now: datetime | None = None) -> str:
    """The pay page for one month of Pro: a fresh one, or the one just opened."""
    now = now or datetime.now(UTC)
    s = get_settings()

    recent = await session.scalar(
        select(Payment)
        .where(
            Payment.user_id == user.id,
            Payment.status == PENDING,
            Payment.url.is_not(None),
            Payment.amount_rub == s.pro_price_rub,
            Payment.created_at > now - REUSE_PAGE,
        )
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    if recent is not None and recent.url:
        return recent.url

    payment = Payment(
        id=uuid.uuid4(), user_id=user.id, amount_rub=s.pro_price_rub, days=s.pro_days,
        status=PENDING,
    )
    session.add(payment)
    await session.commit()

    site = s.public_url.rstrip("/")
    answer = await _request(
        "POST",
        "/v2/transaction/process",
        {
            "paymentDetails": {"amount": s.pro_price_rub, "currency": "RUB"},
            "description": f"gethire Pro на {s.pro_days} дней",
            "return": f"{site}/search?payment=done",
            "failedUrl": f"{site}/search?payment=failed",
            "payload": str(payment.id),
            "orderId": str(payment.id),
            # Platega's anti-fraud keys on a stable payer id.
            "metadata": {"userId": str(user.id), "userName": _who(user)},
        },
    )
    transaction_id, url = answer.get("transactionId"), answer.get("url")
    if not transaction_id or not url:
        log.warning("platega answered without a transaction: %s", str(answer)[:500])
        raise BillingUnavailable("platega answered without a transaction")

    payment.transaction_id = str(transaction_id)
    payment.url = str(url)
    await session.commit()
    return payment.url


def _amount_matches(answer: dict[str, Any], payment: Payment) -> bool:
    details = answer.get("paymentDetails") or {}
    try:
        return abs(float(details.get("amount")) - payment.amount_rub) < 0.01 and (
            str(details.get("currency", "RUB")).upper() == "RUB"
        )
    except (TypeError, ValueError):
        return False


def apply_status(user: User | None, payment: Payment, status: str, now: datetime) -> bool:
    """Move a payment to `status`, granting or taking back its days. True if it moved.

    Idempotent: a repeat of the current status changes nothing, so the second
    and third delivery of one confirmation cannot add a second month.
    """
    if status == payment.status or status not in (CONFIRMED, CANCELED, CHARGEBACKED, PENDING):
        return False

    if status == CONFIRMED and payment.status in (PENDING, CANCELED):
        # Canceled first and confirmed later happens: the payer retried on
        # the same page after a failed attempt.
        payment.status = CONFIRMED
        payment.paid_at = now
        if user is not None:
            start = user.paid_until if user.paid_until and user.paid_until > now else now
            user.paid_until = start + timedelta(days=payment.days)
        return True

    if status == CHARGEBACKED and payment.status == CONFIRMED:
        # The money went back: so do the days, but never before today —
        # days already used are not owed twice.
        payment.status = CHARGEBACKED
        if user is not None and user.paid_until is not None:
            back = user.paid_until - timedelta(days=payment.days)
            user.paid_until = back if back > now else now
        return True

    if status == CANCELED and payment.status == PENDING:
        payment.status = CANCELED
        return True

    # Anything else (a confirmed payment reported pending, say) is a step
    # backwards we do not take on Platega's word alone.
    log.warning("payment %s: ignored %s -> %s", payment.id, payment.status, status)
    return False


async def settle(session: AsyncSession, payment: Payment, now: datetime | None = None) -> bool:
    """Ask Platega where this payment stands and record it. True if anything changed."""
    now = now or datetime.now(UTC)
    if not payment.transaction_id:
        return False
    answer = await _request("GET", f"/transaction/{payment.transaction_id}")
    status = STATUSES.get(str(answer.get("status", "")).upper())
    if status is None:
        log.warning("payment %s: unknown status %r", payment.id, answer.get("status"))
        return False
    if status == CONFIRMED and not _amount_matches(answer, payment):
        # Never grant a month for a payment of some other sum.
        log.error("payment %s: confirmed with a different amount: %s", payment.id,
                  str(answer.get("paymentDetails"))[:200])
        return False

    # Locked, so a callback and a return from the pay page arriving together
    # cannot both see "pending" and both add a month.
    locked = await session.scalar(
        select(Payment)
        .where(Payment.id == payment.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        return False
    user = (
        await session.scalar(
            select(User)
            .where(User.id == locked.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked.user_id
        else None
    )
    moved = apply_status(user, locked, status, now)
    await session.commit()
    return moved


async def settle_transaction(session: AsyncSession, transaction_id: str) -> bool:
    """The callback's half: find our payment by Platega's id and settle it."""
    payment = await session.scalar(
        select(Payment).where(Payment.transaction_id == transaction_id)
    )
    if payment is None:
        log.warning("platega callback for an unknown transaction %s", transaction_id)
        return False
    return await settle(session, payment)


async def settle_recent(session: AsyncSession, user: User, now: datetime | None = None) -> None:
    """The return from the pay page: settle this user's unsettled payments."""
    now = now or datetime.now(UTC)
    rows = await session.scalars(
        select(Payment).where(
            Payment.user_id == user.id,
            Payment.status == PENDING,
            Payment.transaction_id.is_not(None),
            Payment.created_at > now - SYNC_WINDOW,
        )
    )
    for payment in list(rows):
        try:
            await settle(session, payment, now)
        except BillingUnavailable as exc:
            # The callback will still come; the user sees the plan they have.
            log.warning("payment %s not settled on return: %s", payment.id, exc)
