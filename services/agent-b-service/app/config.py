"""
Agent B Service - AI Manager Agent
Configuration settings loaded from environment variables.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Service Identity
    service_name: str = "agent-b-service"
    environment: str = "development"
    debug: bool = False

    # MongoDB
    mongodb_url: str = "mongodb://mongodb:27017"
    mongodb_db_name: str = "agent_b_db"

    # Redis
    redis_url: str = "redis://redis:6379/0"
    redis_ttl_seconds: int = 3600

    # Kafka
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_consumer_group_id: str = "agent-b-evaluation-group"
    kafka_topic_conversation_completed: str = "conversation.completed"
    kafka_topic_evaluation_completed: str = "evaluation.completed"
    kafka_topic_learning_applied: str = "learning.applied"
    kafka_topic_report_generated: str = "report.generated"

    # Qdrant Vector DB
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection_name: str = "knowledge_base"
    qdrant_embedding_dim: int = 1536

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"

    # Downstream Services
    lead_service_url: str = "http://lead-service:8001"
    analytics_service_url: str = "http://analytics-service:8003"
    agent_a_service_url: str = "http://agent-a-service:8002"

    # Sentry
    sentry_dsn: str = ""

    # Evaluation Thresholds
    high_confidence_threshold: float = 0.80
    auto_apply_min_score_threshold: float = 6.0
    critical_mistake_auto_rollback: bool = True

    # Pagination
    default_page_size: int = 20
    max_page_size: int = 100

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
