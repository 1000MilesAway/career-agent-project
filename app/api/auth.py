"""Login and the current account."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from ..core import telegram_login
from ..core.config import get_settings
from ..core.deps import SessionDep, UserDep
from ..core.security import (
    create_access_token,
    create_email_token,
    create_reset_token,
    hash_password,
    password_mark_matches,
    read_email_token,
    read_reset_token,
    verify_password,
)
from ..models import User
from ..services import added, mail
from ..services.spend import plan_of

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    # Null for an account that signed up with Telegram.
    email: EmailStr | None
    # Null until the user sets one; the cabinet shows the email instead.
    name: str | None
    is_admin: bool
    consent_at: datetime | None
    # "free" or "paid". How much of the quota is used lives in /api/state.
    plan: str
    # "@handle", or the first name for an account without one; null = not linked.
    telegram_name: str | None

    @classmethod
    def of(cls, user: User) -> UserResponse:
        return cls(
            email=user.email,
            name=user.name,
            is_admin=user.is_admin,
            consent_at=user.consent_at,
            plan=plan_of(user),
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

    if (
        user is None
        or not user.is_active
        or user.password_hash is None
        or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )
    # After the password check, so it tells nothing to someone without it.
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Почта ещё не подтверждена — откройте ссылку из письма",
        )

    return TokenResponse(access_token=create_access_token(user.id))


# A second letter to the same address waits this long. Resend's free plan is
# 100 letters a day for the whole service, and a held "send again" button
# should not be able to spend them.
RESEND_AFTER = timedelta(seconds=60)


class SignupConfig(BaseModel):
    # False when mail is not configured: the login page then offers no signup.
    enabled: bool


class SignupRequest(BaseModel):
    email: EmailStr
    # bcrypt reads only the first 72 bytes; a longer password would be
    # accepted and silently cut.
    password: str = Field(min_length=8, max_length=72)


class SignupResponse(BaseModel):
    email: EmailStr
    detail: str


class VerifyRequest(BaseModel):
    token: str


@router.get("/signup", response_model=SignupConfig)
async def signup_config() -> SignupConfig:
    """Whether signing up by email is on. Public, like the Telegram config."""
    return SignupConfig(enabled=mail.mail_enabled())


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_202_ACCEPTED)
async def signup(body: SignupRequest, session: SessionDep) -> SignupResponse:
    """Create an account and send the link that confirms its address.

    The account cannot log in until the link is opened. Signing up again for
    an unconfirmed address replaces the password and sends a new letter: the
    first attempt may have been a typo, or somebody else's — only the owner of
    the inbox can finish either one.

    A confirmed address gets the same answer and no letter, so the form does
    not tell a stranger which addresses have accounts here. Its owner logs in.
    """
    if not mail.mail_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Регистрация по почте не настроена")
    email = body.email.lower()
    sent = SignupResponse(
        email=email,
        detail="Письмо со ссылкой отправлено — откройте его, чтобы войти",
    )

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is not None and user.email_verified_at is not None:
        return sent

    now = datetime.now(UTC)
    if (
        user is not None
        and user.verification_sent_at is not None
        and now - user.verification_sent_at < RESEND_AFTER
    ):
        wait = int((RESEND_AFTER - (now - user.verification_sent_at)).total_seconds()) + 1
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Письмо только что ушло — повторить можно через {wait} с",
        )

    password_hash = hash_password(body.password)
    if user is None:
        user = User(email=email, password_hash=password_hash)
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            # The same address submitted twice at once: the other request won
            # and is sending its letter.
            await session.rollback()
            return sent
    else:
        user.password_hash = password_hash

    link = f"{get_settings().public_url.rstrip('/')}/verify?token=" + create_email_token(
        user.id, email, password_hash
    )
    subject, text, html_body = mail.verification_letter(link)
    try:
        await mail.send_email(email, subject, text, html_body)
    except mail.MailUnavailable:
        await session.rollback()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Не получилось отправить письмо — попробуйте позже или войдите через Telegram",
        ) from None
    user.verification_sent_at = now
    await session.commit()
    return sent


@router.post("/verify", response_model=TokenResponse)
async def verify_email(body: VerifyRequest, session: SessionDep) -> TokenResponse:
    """Confirm the address from a letter's link and log in.

    Opening a link twice is fine: an already confirmed account just logs in.
    A link from before a repeat signup no longer matches the password and is
    refused, like an expired one — one message for every failure.
    """
    refused = HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "Ссылка устарела или повреждена — зарегистрируйтесь ещё раз, придёт новое письмо",
    )
    claims = read_email_token(body.token)
    if claims is None:
        raise refused
    user_id, email, mark = claims
    user = await session.get(User, user_id)
    if user is None or user.email != email or not password_mark_matches(mark, user.password_hash):
        raise refused
    if not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Учётка выключена администратором")
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        await session.commit()
    return TokenResponse(access_token=create_access_token(user.id))


class ResetRequest(BaseModel):
    email: EmailStr


class ResetConfirm(BaseModel):
    token: str
    # The same limits as at signup.
    password: str = Field(min_length=8, max_length=72)


@router.post("/password-reset", response_model=SignupResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(body: ResetRequest, session: SessionDep) -> SignupResponse:
    """Send a link that sets a new password.

    The answer is the same whether a letter went or not: unknown address,
    Telegram-only account, disabled account, a letter sent under a minute
    ago. A reset form that told them apart would be a directory of who has
    an account here. The throttle is silent for the same reason — the letter
    from a minute ago is still the one to open.

    An unconfirmed signup may reset too: the link proves the inbox, which is
    all the confirmation letter would have proved.
    """
    if not mail.mail_enabled():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Почта не настроена")
    email = body.email.lower()
    sent = SignupResponse(
        email=email,
        detail="Если такая учётка есть, письмо со ссылкой уже в пути",
    )

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if (
        user is None
        or not user.is_active
        or user.password_hash is None
        or (
            user.verification_sent_at is not None
            and now - user.verification_sent_at < RESEND_AFTER
        )
    ):
        return sent

    link = f"{get_settings().public_url.rstrip('/')}/reset-password?token=" + create_reset_token(
        user.id, email, user.password_hash
    )
    subject, text, html_body = mail.password_reset_letter(link)
    try:
        await mail.send_email(email, subject, text, html_body)
    except mail.MailUnavailable:
        # Hidden too: only a known address ever reaches the send, so a 503
        # here would tell it apart — and Resend's daily quota is something a
        # stranger can exhaust with signups. Not stamped, so a retry sends.
        log.warning("password reset letter was not sent", exc_info=True)
        return sent
    user.verification_sent_at = now
    await session.commit()
    return sent


@router.post("/password-reset/confirm", response_model=TokenResponse)
async def confirm_password_reset(body: ResetConfirm, session: SessionDep) -> TokenResponse:
    """Set the new password from a reset link, and log in with it.

    The link works once: the new password changes the mark it is bound to.
    Every token issued before the reset stops working (see core/deps.py).
    """
    refused = HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "Ссылка устарела или уже использована — запросите сброс ещё раз",
    )
    claims = read_reset_token(body.token)
    if claims is None:
        raise refused
    user_id, email, mark = claims
    user = await session.get(User, user_id)
    if user is None or user.email != email or not password_mark_matches(mark, user.password_hash):
        raise refused
    if not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Учётка выключена администратором")

    now = datetime.now(UTC)
    user.password_hash = hash_password(body.password)
    user.password_changed_at = now
    if user.email_verified_at is None:
        user.email_verified_at = now
    await session.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
async def me(user: UserDep) -> UserResponse:
    return UserResponse.of(user)


class ProfileUpdate(BaseModel):
    # Blank clears it. The cap is the column's; a longer name is refused
    # rather than cut, so the user sees exactly what was saved.
    name: str | None = Field(default=None, max_length=80)


@router.patch("/me", response_model=UserResponse)
async def update_me(body: ProfileUpdate, user: UserDep, session: SessionDep) -> UserResponse:
    """Change what the user typed in their profile. Only fields sent are touched."""
    if "name" in body.model_fields_set:
        user.name = (body.name or "").strip() or None
    await session.commit()
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


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(user: UserDep, session: SessionDep) -> None:
    """Delete the account and everything in it: the right to erasure.

    One cascading delete, as decided 2026-09-22 for «удалить аккаунт /
    отозвать согласие»: every table hangs off `users.id` with ON DELETE
    CASCADE, so resumes, vacancies, evaluations (their reports quote the CV),
    documents, jobs and the chat go with the row. Losing the bought reports is
    the point of the operation, not a side effect.

    An admin is refused: the owner's account runs the service, and one
    mis-click in the cabinet must not take it down. Admins are removed from
    the CLI.
    """
    if user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Учётку администратора из кабинета не удалить",
        )
    # SQL, not session.delete(): the ORM would lazy-load every relationship
    # to delete it by hand, which async SQLAlchemy refuses. The database's
    # own cascades do the same work in one statement.
    # Pasted vacancy texts hang off the shared pool, not the user, so the
    # cascade does not reach them; they are the user's words all the same.
    await added.delete_texts(session, user.id)
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()


class TelegramConfig(BaseModel):
    # Null when Telegram login is off: the cabinet then shows no button.
    client_id: int | None
    # Goes into the id_token Telegram signs; see core/telegram_login.py.
    nonce: str | None


class TelegramToken(BaseModel):
    id_token: str


async def _telegram_identity(body: TelegramToken) -> telegram_login.TelegramIdentity:
    """Verify an id_token from Telegram's popup.

    A bad, stale or foreign token is one 401 whatever the reason, as with the
    password: the popup produces a fresh token on the next click anyway.
    """
    client_id = telegram_login.client_id()
    if client_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вход через Telegram не настроен")
    try:
        # Off the event loop: fetching Telegram's keys is blocking urllib.
        identity = await asyncio.to_thread(
            telegram_login.verify_id_token, body.id_token, client_id
        )
    except telegram_login.TelegramUnavailable:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Telegram сейчас не отвечает — попробуйте через минуту или войдите по email",
        ) from None
    if identity is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Telegram не подтвердил вход — попробуйте ещё раз",
        )
    return identity


@router.get("/telegram", response_model=TelegramConfig)
async def telegram_config() -> TelegramConfig:
    """What the login button needs. Public: the login page asks before anyone logs in."""
    client_id = telegram_login.client_id()
    if client_id is None:
        return TelegramConfig(client_id=None, nonce=None)
    return TelegramConfig(client_id=client_id, nonce=telegram_login.issue_nonce())


@router.post("/telegram", response_model=TokenResponse)
async def telegram_login_endpoint(body: TelegramToken, session: SessionDep) -> TokenResponse:
    """Log in with Telegram; an account seen for the first time is created.

    Signing up takes nothing but the Telegram confirmation: no email, no
    password. What bounds a stranger's cost is the free plan's quota, per account.
    A disabled account is refused rather than re-created — the Telegram id is
    still its, so a fresh account would undo `user-off`.
    """
    identity = await _telegram_identity(body)
    result = await session.execute(select(User).where(User.telegram_id == identity.id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=identity.id, telegram_name=identity.name)
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            # Two popups for one Telegram account at once: the other won.
            await session.rollback()
            result = await session.execute(select(User).where(User.telegram_id == identity.id))
            user = result.scalar_one()
    if not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Учётка выключена администратором")
    # The handle can change in Telegram; keep the displayed one current.
    if user.telegram_name != identity.name:
        user.telegram_name = identity.name
        await session.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/telegram/link", response_model=UserResponse)
async def telegram_link(body: TelegramToken, user: UserDep, session: SessionDep) -> UserResponse:
    """Link the Telegram account the token vouches for to the current user.

    The user's own confirmation in Telegram's popup is the "yes" — nothing is
    linked without it. One Telegram account belongs to one user: taking it
    over from another would hand that user's login to whoever clicked.
    """
    identity = await _telegram_identity(body)
    result = await session.execute(select(User).where(User.telegram_id == identity.id))
    owner = result.scalar_one_or_none()
    if owner is not None and owner.id != user.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Этот Telegram уже привязан к другой учётке — сначала отвяжите его там",
        )
    user.telegram_id = identity.id
    user.telegram_name = identity.name
    await session.commit()
    return UserResponse.of(user)


@router.delete("/telegram/link", response_model=UserResponse)
async def telegram_unlink(user: UserDep, session: SessionDep) -> UserResponse:
    """Unlink Telegram, when a password is there to log in with instead.

    An account that signed up with Telegram has nothing else: unlinking would
    leave an account nobody can reach, with the user's resume in it.
    """
    if user.password_hash is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Telegram — единственный вход в эту учётку, отвязать его нельзя",
        )
    user.telegram_id = None
    user.telegram_name = None
    await session.commit()
    return UserResponse.of(user)
