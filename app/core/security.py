"""Passwords and access tokens.

Deliberately small. Email exists only to confirm the address of a
self-signup; there is still no password reset: a lost password is an admin
re-running the CLI.
"""

from __future__ import annotations

import hashlib
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

# The claim that tells an email-confirmation token from an access token. Both
# are signed with one key, so without it a link from a letter would work as a
# login token and a login token as a confirmation.
_EMAIL_TOKEN_TYPE = "verify_email"


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
        # Access tokens carry no type; anything typed is some other token.
        if "typ" in payload:
            return None
        return uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def _password_mark(password_hash: str) -> str:
    """A short fingerprint of the stored hash, to bind a link to one signup.

    Signing up again for a still unconfirmed address sets a new password; the
    mark then changes and links from earlier letters stop working. Otherwise a
    link sent for somebody else's password would confirm the account with it.
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def create_email_token(user_id: uuid.UUID, email: str, password_hash: str) -> str:
    """The token in a confirmation letter: this account, this address, this password."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "typ": _EMAIL_TOKEN_TYPE,
        "email": email,
        "pwd": _password_mark(password_hash),
        "iat": now,
        "exp": now + timedelta(hours=settings.email_token_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def read_email_token(token: str) -> tuple[uuid.UUID, str, str] | None:
    """(user id, email, password mark) from a confirmation token, or None.

    One None for every failure, as with access tokens. The caller compares the
    email and the mark against the account as it is now.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("typ") != _EMAIL_TOKEN_TYPE:
            return None
        return uuid.UUID(payload["sub"]), payload["email"], payload["pwd"]
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        return None


def password_mark_matches(mark: str, password_hash: str | None) -> bool:
    return password_hash is not None and mark == _password_mark(password_hash)

