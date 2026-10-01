"""Settings, read from the environment once.

Everything that differs between a laptop and the server lives here, so nothing
downstream has to know whether a value came from `.env` or from the process
environment.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://career:career@localhost:5433/career"

    # Signing key for access tokens. The default is deliberately unusable in
    # production: `app.main` refuses to start on it when DEBUG is off, because
    # a predictable key lets anyone mint a token for any account.
    secret_key: str = "change-me"
    access_token_hours: int = 72

    # LLM settings are read by core.llm from the same .env; repeated here only
    # where the API needs to report them (the /health payload names the model).
    deepseek_model: str = "deepseek-flash"

    # Quotas per plan, in USD at the off-peak price; 0 or less switches that
    # window off. The owner's key pays for every call, so a public host needs
    # them on. An evaluation is ~$0.0018, a full package ~$0.0085: the free day
    # is ~11 evaluations, the paid day ~55. Users see a percentage, never these
    # numbers, so they can be retuned without a word of the interface changing.
    # Decided 2026-10-01, see docs/decisions.md.
    free_day_usd: float = 0.02
    free_week_usd: float = 0.06
    paid_day_usd: float = 0.10
    paid_week_usd: float = 0.30

    # Telegram Login (OIDC). The client id is the bot id, read from the token
    # when not given; with neither set, Telegram login is off and the cabinet
    # shows no button. The site's URL must be in the bot's Allowed URLs
    # (@BotFather -> bot -> Login Widget), or Telegram refuses the popup.
    telegram_bot_token: str = ""
    telegram_client_id: int | None = None

    # Email via Resend (https://resend.com), used for now only to confirm the
    # address of a self-signup. With either value empty, signing up by email is
    # off and the login page offers only Telegram. The sender's domain must be
    # verified in Resend: without it Resend delivers only to the owner's own
    # address, and every other signup would wait for a letter that never comes.
    resend_api_key: str = ""
    mail_from: str = ""
    # Where the link in a letter points. The cabinet is what opens it, so this
    # is the site's address, not the API's.
    public_url: str = "https://career-helper.site"
    # How long a confirmation link works.
    email_token_hours: int = 24

    debug: bool = False

    # Which revision is actually running, reported by /api/health. Set by the
    # deploy script from the checkout on the host; the image cannot work it out
    # for itself, because `.git` is deliberately not copied into it.
    #
    # "unknown" is the honest answer for a run started by hand, and it must stay
    # distinguishable from a stale sha: a deploy that silently kept serving the
    # previous revision is exactly what this field exists to catch.
    git_sha: str = "unknown"


@lru_cache
def get_settings() -> Settings:
    return Settings()
