"""Telegram Login (OpenID Connect): who a Telegram id_token vouches for.

The page gets the id_token from Telegram's own popup (telegram-login.js) and
hands it to the API; nothing it says is trusted until the checks here pass.
This replaced the iframe Login Widget, which Telegram archived in favour of
OIDC — see docs/decisions.md.

What is checked, and why each one:

- the signature, against Telegram's published keys (JWKS). Telegram lets the
  bot owner switch the algorithm in @BotFather, so every one it advertises is
  accepted — a fixed RS256 would break login on that click;
- `iss` is Telegram and `aud` is our bot: a token minted for another site's
  bot is genuine, and must still not log in here;
- `nonce` is one we issued within the hour. Without it a leaked token logs in
  for as long as Telegram's own `exp` allows.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from jwt import PyJWKClient

from .config import Settings, get_settings

ISSUER = "https://oauth.telegram.org"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
ALGORITHMS = ["RS256", "ES256", "EdDSA", "ES256K"]

# The login page fetches a nonce when it opens and refreshes it every half
# hour, so an hour covers a page left open without keeping one alive for days.
NONCE_TTL = timedelta(hours=1)
_NONCE_PURPOSE = "telegram-login"

# Keys change rarely; an hour's cache keeps a login from costing a round trip
# to Telegram. PyJWKClient refetches on its own when a token names a key id it
# has not seen, so a rotation does not wait for the cache to expire.
_jwks = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600, timeout=10)


class TelegramUnavailable(Exception):
    """Telegram's keys could not be fetched: our outage, not the user's error."""


@dataclass(frozen=True)
class TelegramIdentity:
    id: int
    # "@handle", or the name for an account without one — for display only.
    name: str


def client_id(settings: Settings | None = None) -> int | None:
    """The bot's OIDC client id; None when Telegram login is switched off.

    Telegram's client id is the bot id, which is also the part of the bot
    token before the colon — so a configured token is enough, and
    TELEGRAM_CLIENT_ID exists only to override that.
    """
    settings = settings or get_settings()
    if settings.telegram_client_id:
        return settings.telegram_client_id
    head = settings.telegram_bot_token.split(":", 1)[0]
    return int(head) if head.isdigit() else None


def issue_nonce(now: datetime | None = None) -> str:
    """A nonce the API can recognise later without storing it: signed, dated."""
    now = now or datetime.now(UTC)
    payload = {"p": _NONCE_PURPOSE, "n": secrets.token_urlsafe(16), "exp": now + NONCE_TTL}
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def _nonce_is_ours(nonce: object) -> bool:
    if not isinstance(nonce, str):
        return False
    try:
        payload = jwt.decode(nonce, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return payload.get("p") == _NONCE_PURPOSE


def _signing_key(token: str):
    """Telegram's public key for this token. Replaced in tests."""
    try:
        return _jwks.get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientConnectionError as e:
        raise TelegramUnavailable(str(e)) from e


def verify_id_token(token: str, expected_client_id: int) -> TelegramIdentity | None:
    """The Telegram user an id_token vouches for, or None if it does not.

    Blocking (the key fetch is plain urllib): call it off the event loop.
    Raises TelegramUnavailable when the keys cannot be fetched — reporting
    that as "login failed" would send the user to retry something that is
    not theirs to fix.
    """
    try:
        key = _signing_key(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=ALGORITHMS,
            issuer=ISSUER,
            # aud is checked by hand below: Telegram's docs show it both as a
            # number and as a string, and PyJWT rejects a numeric aud outright.
            options={"verify_aud": False, "require": ["exp", "iat", "iss", "aud"]},
        )
    except TelegramUnavailable:
        raise
    except (jwt.PyJWTError, ValueError, TypeError):
        return None

    aud = claims["aud"]
    audiences = aud if isinstance(aud, list) else [aud]
    if str(expected_client_id) not in {str(a) for a in audiences}:
        return None
    if not _nonce_is_ours(claims.get("nonce")):
        return None

    # `id` is the Telegram user id, the same number the old widget reported;
    # `sub` is an OIDC subject that is not documented to equal it.
    try:
        telegram_id = int(claims["id"])
    except (KeyError, TypeError, ValueError):
        return None
    username = claims.get("preferred_username")
    name = f"@{username}" if username else str(claims.get("name") or telegram_id)
    return TelegramIdentity(id=telegram_id, name=name[:128])
