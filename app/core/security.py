"""Passwords and access tokens.

Deliberately small. There is no email in the MVP, which means no address
confirmation and no password reset: a lost password is an admin re-running the
CLI. That constraint is what keeps this file short, not an oversight.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from .config import get_settings

# bcrypt, as docs/03-architecture.md specifies — named explicitly rather than
# via PasswordHash.recommended(), which resolves to argon2 and would fail at
# import on a bcrypt-only install.
#
# Listing it as the only hasher also fixes the verification set: a hash in any
# other scheme fails to verify instead of being accepted silently. Adding
# argon2 later means adding it here, in this order, so existing bcrypt hashes
# keep verifying.
_hasher = PasswordHash((BcryptHasher(),))

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password. A malformed stored hash is a failed login, not a crash."""
    try:
        return _hasher.verify(password, password_hash)
    except Exception:
        return False


def create_access_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(hours=settings.access_token_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def read_access_token(token: str) -> uuid.UUID | None:
    """The user id a token carries, or None if it is expired, forged or junk.

    Returns None rather than raising: every failure here means the same thing
    to the caller — this request is not authenticated — and distinguishing
    "expired" from "bad signature" in the response would tell an attacker
    which of the two they achieved.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None
