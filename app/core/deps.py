"""FastAPI dependencies: who is asking, and with which CV.

Error texts are for the user, so they are Russian; everything else stays
English, per the project convention.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Resume, User
from .db import get_session
from .security import read_access_claims

# auto_error=False so a missing header produces our 401 with a Russian message
# rather than FastAPI's default English one.
_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    """The authenticated account, or 401.

    A deactivated account is rejected here rather than at login: the token
    outlives the decision to switch the account off, and access has to stop
    when the flag flips, not when the token expires.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Нужно войти",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    claims = read_access_claims(credentials.credentials)
    if claims is None:
        raise unauthorized
    user_id, issued_at = claims

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized
    # A reset logs out every session from before it. Whole seconds, as `iat`
    # is: the token handed out by the reset itself carries the same second.
    if user.password_changed_at is not None and issued_at < int(
        user.password_changed_at.timestamp()
    ):
        raise unauthorized
    return user


UserDep = Annotated[User, Depends(current_user)]


async def current_admin(user: UserDep) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нужны права администратора")
    return user


AdminDep = Annotated[User, Depends(current_admin)]


async def active_resume(session: SessionDep, user: UserDep) -> Resume:
    """The CV everything is evaluated against.

    404 rather than an empty result: without a CV there is no profile to score
    against, and every paid step downstream would be judging nothing. The demo
    made the same call by refusing to search before a resume was uploaded.
    """
    result = await session.execute(
        select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True))
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Сначала загрузите резюме",
        )
    return resume


ResumeDep = Annotated[Resume, Depends(active_resume)]


async def optional_resume(session: SessionDep, user: UserDep) -> Resume | None:
    """The active CV, or None where a step works without one."""
    result = await session.execute(
        select(Resume).where(Resume.user_id == user.id, Resume.is_active.is_(True))
    )
    return result.scalar_one_or_none()


OptionalResumeDep = Annotated[Resume | None, Depends(optional_resume)]
