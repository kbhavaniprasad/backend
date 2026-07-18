"""
Voice Service Configuration
Retell AI is the primary AI voice agent provider.
Twilio is used as the PSTN telephony layer (SIP trunk) beneath Retell.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Retell AI (Primary Voice Agent) ──────────────────────────────────────
    retell_api_key: str = "key_3acd90982316f78ff63c2367469b"
    retell_agent_id: str = "agent_cbc4d9dffbfd3df155cccb4828"
    retell_webhook_secret: str = ""
    retell_base_url: str = "https://api.retellai.com"

    # ── Twilio (PSTN telephony — SIP trunk for Retell outbound calls) ─────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # ── Infrastructure ────────────────────────────────────────────────────────
    mongodb_url: str = "mongodb://platform_user:platform_secret@mongodb:27017/ai_platform?authSource=admin"
    redis_url: str = "redis://:redis_secret@redis:6379"
    kafka_bootstrap_servers: str = "kafka:9092"

    # ── Service URLs ──────────────────────────────────────────────────────────
    lead_service_url: str = "http://lead-service:8002"
    agent_a_service_url: str = "http://agent-a-service:8003"

    # ── Observability ─────────────────────────────────────────────────────────
    sentry_dsn: str = ""
    environment: str = "development"

    # ── Call Limits ───────────────────────────────────────────────────────────
    max_concurrent_calls: int = 500
    call_timeout_seconds: int = 120
    max_retries_per_lead: int = 3

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
