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

    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
