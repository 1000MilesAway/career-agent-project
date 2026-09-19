"""Login and the current account."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from ..core.deps import SessionDep, UserDep
from ..core.security import create_access_token, verify_password
from ..models import User

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
    return UserResponse(
        email=user.email,
        is_admin=user.is_admin,
        consent_at=user.consent_at,
        spent_rub=float(user.spent_rub),
    )


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
    return UserResponse(
        email=user.email,
        is_admin=user.is_admin,
        consent_at=user.consent_at,
        spent_rub=float(user.spent_rub),
    )
