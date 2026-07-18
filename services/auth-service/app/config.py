"""
config.py — Application Settings for AI Platform Auth Service.

Loads configuration from environment variables / .env file using pydantic-settings.
All sensitive values (secrets, DSNs) are injected via environment so that no
credentials are ever hard-coded in source.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration class.

    Values are read (in priority order) from:
      1. Actual environment variables
      2. A .env file in the working directory
      3. The declared defaults below
    """

    # ------------------------------------------------------------------ #
    # Database                                                             #
    # ------------------------------------------------------------------ #
    database_url: str = Field(
        ...,
        description="PostgreSQL async connection URL, e.g. "
        "postgresql+asyncpg://user:pass@host:5432/dbname",
    )

    # ------------------------------------------------------------------ #
    # Redis                                                                #
    # ------------------------------------------------------------------ #
    redis_url: str = Field(
        ...,
        description="Redis connection URL, e.g. redis://localhost:6379/0",
    )

    # ------------------------------------------------------------------ #
    # JWT                                                                  #
    # ------------------------------------------------------------------ #
    jwt_secret: str = Field(
        ...,
        description="Secret key used to sign access tokens (HS256).",
    )
    jwt_refresh_secret: str = Field(
        ...,
        description="Separate secret key used to sign refresh tokens.",
    )
    jwt_expire_minutes: int = Field(
        default=60,
        description="Access-token lifetime in minutes.",
    )
    jwt_refresh_expire_days: int = Field(
        default=7,
        description="Refresh-token lifetime in days.",
    )

    # ------------------------------------------------------------------ #
    # Google OAuth2                                                        #
    # ------------------------------------------------------------------ #
    google_client_id: str = Field(
        default="",
        description="Google OAuth2 client ID (optional).",
    )
    google_client_secret: str = Field(
        default="",
        description="Google OAuth2 client secret (optional).",
    )

    # ------------------------------------------------------------------ #
    # Observability                                                        #
    # ------------------------------------------------------------------ #
    sentry_dsn: str = Field(
        default="",
        description="Sentry Data Source Name. Leave empty to disable.",
    )
    environment: str = Field(
        default="development",
        description="Runtime environment tag (development / staging / production).",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached singleton Settings instance.

    Using @lru_cache means the .env file is parsed exactly once per process,
    which is safe for production and also makes unit-testing straightforward —
    call ``get_settings.cache_clear()`` before overriding env vars in tests.
    """
    return Settings()  # type: ignore[call-arg]
