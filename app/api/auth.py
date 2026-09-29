"""Login and the current account."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from ..core.config import get_settings
from ..core.deps import SessionDep, UserDep
from ..core.security import create_access_token, verify_password, verify_telegram_login
from ..models import User
from ..services.spend import remaining_rub

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    email: EmailStr
    is_admin: bool
    consent_at: datetime | None
    spent_rub: float
    # What is left of the budget; null when no limit is configured. Shown so
    # the ceiling is visible before it is hit, rather than arriving as a
    # surprise 402 mid-click.
    remaining_rub: float | None
    # "@handle", or the first name for an account without one; null = not linked.
    telegram_name: str | None

    @classmethod
    def of(cls, user: User) -> UserResponse:
        return cls(
            email=user.email,
            is_admin=user.is_admin,
            consent_at=user.consent_at,
            spent_rub=float(user.spent_rub),
            remaining_rub=remaining_rub(user),
            telegram_name=user.telegram_name if user.telegram_id is not None else None,
        )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: SessionDep) -> TokenResponse:
    """Exchange email and password for a token.

    One message for every failure — unknown address, wrong password, disabled
    account. Saying which one was wrong tells an attacker whether an address
    is registered here, and a resume site should not leak that.
    """
    result = await session.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )

    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
async def me(user: UserDep) -> UserResponse:
    return UserResponse.of(user)


@router.post("/consent", response_model=UserResponse)
async def give_consent(user: UserDep, session: SessionDep) -> UserResponse:
    """Record consent to process personal data.

    A resume is personal data, so this is evidence rather than a checkbox: it
    records that this account agreed and when. Already-given consent is not
    re-stamped — the first date is the one that matters.
    """
    if user.consent_at is None:
        user.consent_at = datetime.now(UTC)
        await session.commit()
    return UserResponse.of(user)


class TelegramConfig(BaseModel):
    # Null when the bot is not configured: the cabinet then shows no button.
    bot_username: str | None


def _telegram_identity(data: dict[str, Any]) -> tuple[int, str]:
    """Verify a Login Widget answer; the Telegram id and a display name.

    A bad or stale signature is one 401 whatever the reason, as with the
    password: the widget produces a fresh answer on the next click anyway.
    """
    settings = get_settings()
    if not (settings.telegram_bot_token and settings.telegram_bot_username):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вход через Telegram не настроен")
    telegram_id = verify_telegram_login(data, settings.telegram_bot_token)
    if telegram_id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Telegram не подтвердил вход — попробуйте ещё раз",
        )
    username = data.get("username")
    name = f"@{username}" if username else str(data.get("first_name") or telegram_id)
    return telegram_id, name[:128]


@router.get("/telegram", response_model=TelegramConfig)
async def telegram_config() -> TelegramConfig:
    """Which bot the login widget is drawn for. Public: the login page needs it."""
    settings = get_settings()
    enabled = bool(settings.telegram_bot_token and settings.telegram_bot_username)
    return TelegramConfig(bot_username=settings.telegram_bot_username if enabled else None)


@router.post("/telegram", response_model=TokenResponse)
async def telegram_login(data: dict[str, Any], session: SessionDep) -> TokenResponse:
    """Log in with a Telegram account linked to a user beforehand.

    An unknown Telegram account is refused, not registered: accounts are
    still created by the admin. Saying "not linked" leaks nothing — only the
    owner of that Telegram account can produce a signed answer for it.
    """
    telegram_id, name = _telegram_identity(data)
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Этот Telegram не привязан к учётке. Войдите по email и привяжите его "
            "в кабинете.",
        )
    # The handle can change in Telegram; keep the displayed one current.
    if user.telegram_name != name:
        user.telegram_name = name
        await session.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/telegram/link", response_model=UserResponse)
async def telegram_link(data: dict[str, Any], user: UserDep, session: SessionDep) -> UserResponse:
    """Link the Telegram account the widget vouched for to the current user.

    The user's own click in the widget is the confirmation — nothing is linked
    without it. One Telegram account belongs to one user: taking it over from
    another would hand that user's login to whoever clicked.
    """
    telegram_id, name = _telegram_identity(data)
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    owner = result.scalar_one_or_none()
    if owner is not None and owner.id != user.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Этот Telegram уже привязан к другой учётке — сначала отвяжите его там",
        )
    user.telegram_id = telegram_id
    user.telegram_name = name
    await session.commit()
    return UserResponse.of(user)


@router.delete("/telegram/link", response_model=UserResponse)
async def telegram_unlink(user: UserDep, session: SessionDep) -> UserResponse:
    """Unlink Telegram. The password still logs in, so this cannot lock anyone out."""
    user.telegram_id = None
    user.telegram_name = None
    await session.commit()
    return UserResponse.of(user)
