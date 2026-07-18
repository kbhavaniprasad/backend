"""
app/config.py – Lead Service configuration via Pydantic-Settings.

All values are read from environment variables (or a .env file).
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────────
    app_name: str = "AI Platform Lead Service"
    environment: str = "development"          # development | staging | production
    debug: bool = False
    log_level: str = "INFO"

    # ── PostgreSQL (asyncpg) ──────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://lead_user:lead_pass@localhost:5432/lead_db"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/1"
    redis_ttl: int = 3600  # seconds – default cache TTL

    # ── Kafka ─────────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_client_id: str = "lead-service"
    kafka_topic_lead_created: str = "lead.created"
    kafka_topic_lead_status_changed: str = "lead.status_changed"

    # ── JWT / Auth ────────────────────────────────────────────────────────────
    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION_super_secret_key"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60

    # ── Facebook Lead Ads ─────────────────────────────────────────────────────
    facebook_verify_token: str = "CHANGE_ME_facebook_verify_token"
    facebook_app_secret: str = "CHANGE_ME_facebook_app_secret"
    facebook_access_token: str = "CHANGE_ME_facebook_access_token"
    facebook_api_version: str = "v19.0"

    # ── Google Ads ────────────────────────────────────────────────────────────
    google_webhook_secret: str = "CHANGE_ME_google_webhook_secret"

    # ── LinkedIn ──────────────────────────────────────────────────────────────
    linkedin_webhook_secret: str = "CHANGE_ME_linkedin_webhook_secret"

    # ── WhatsApp / Meta ───────────────────────────────────────────────────────
    whatsapp_verify_token: str = "CHANGE_ME_whatsapp_verify_token"
    whatsapp_app_secret: str = "CHANGE_ME_whatsapp_app_secret"

    # ── Sentry ────────────────────────────────────────────────────────────────
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1

    # ── Pagination defaults ───────────────────────────────────────────────────
    default_page_size: int = 20
    max_page_size: int = 100

    # ── Bulk import ───────────────────────────────────────────────────────────
    bulk_import_max_records: int = 500


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
