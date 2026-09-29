"""Passwords and access tokens.

Deliberately small. There is no email in the MVP, which means no address
confirmation and no password reset: a lost password is an admin re-running the
CLI. That constraint is what keeps this file short, not an oversight.
"""

from __future__ import annotations

import hashlib
import hmac
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


# How long a Telegram widget answer stays usable. The signature has no nonce,
# so a captured payload logs in for as long as this lasts; an hour covers a
# slow click-through without leaving a day-long replay window.
TELEGRAM_AUTH_MAX_AGE = timedelta(hours=1)


def verify_telegram_login(
    data: dict[str, object], bot_token: str, now: datetime | None = None
) -> int | None:
    """The Telegram user id a Login Widget answer vouches for, or None.

    The check is Telegram's documented one: every field but `hash`, sorted and
    joined as "key=value" lines, HMAC-SHA256'd with SHA256(bot token) as the
    key. All received fields go into the check, not a fixed list — a field
    Telegram adds later would otherwise fail every login, or worse, pass
    unsigned if the list were used to drop it.
    """
    received = data.get("hash")
    if not bot_token or not isinstance(received, str):
        return None

    check = "\n".join(f"{k}={data[k]}" for k in sorted(data) if k != "hash")
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None

    try:
        signed_at = datetime.fromtimestamp(int(data["auth_date"]), UTC)
        user_id = int(data["id"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if (now or datetime.now(UTC)) - signed_at > TELEGRAM_AUTH_MAX_AGE:
        return None
    return user_id
