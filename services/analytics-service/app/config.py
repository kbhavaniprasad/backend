from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://platform_user:platform_secret@postgres:5432/ai_platform"
    mongodb_url: str = "mongodb://platform_user:platform_secret@mongodb:27017/ai_platform?authSource=admin"
    redis_url: str = "redis://:redis_secret@redis:6379"
    kafka_bootstrap_servers: str = "kafka:9092"
    sentry_dsn: str = ""
    environment: str = "development"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
