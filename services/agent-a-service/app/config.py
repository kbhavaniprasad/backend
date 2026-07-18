"""
Configuration settings for Agent A - Lead Engagement Service.
Uses pydantic-settings to load from environment variables / .env file.
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """
    All configuration is read from environment variables.
    Default values are provided for local development only.
    """

    # ------------------------------------------------------------------ #
    # MongoDB
    # ------------------------------------------------------------------ #
    mongodb_url: str = Field(
        default="mongodb://localhost:27017",
        description="MongoDB connection URL (supports replica-set URIs).",
    )
    mongodb_db_name: str = Field(
        default="agent_service",
        description="MongoDB database name used by this service.",
    )

    # ------------------------------------------------------------------ #
    # Redis
    # ------------------------------------------------------------------ #
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL.",
    )

    # ------------------------------------------------------------------ #
    # Kafka
    # ------------------------------------------------------------------ #
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Comma-separated list of Kafka broker addresses.",
    )
    kafka_consumer_group_id: str = Field(
        default="agent-a-service",
        description="Kafka consumer group identifier.",
    )

    # ------------------------------------------------------------------ #
    # Qdrant (Vector DB)
    # ------------------------------------------------------------------ #
    qdrant_url: str = Field(
        default="http://localhost:6333",
        description="Qdrant REST API base URL.",
    )
    qdrant_api_key: str | None = Field(
        default=None,
        description="Optional Qdrant API key for cloud deployments.",
    )

    # ------------------------------------------------------------------ #
    # OpenAI
    # ------------------------------------------------------------------ #
    openai_api_key: str = Field(
        description="OpenAI API key — must be set via environment variable.",
    )
    openai_model: str = Field(
        default="gpt-4o",
        description="Primary LLM model used for conversation handling.",
    )
    openai_embedding_model: str = Field(
        default="text-embedding-ada-002",
        description="Embedding model used for RAG retrieval.",
    )

    # ------------------------------------------------------------------ #
    # Twilio
    # ------------------------------------------------------------------ #
    twilio_account_sid: str = Field(
        description="Twilio Account SID.",
    )
    twilio_auth_token: str = Field(
        description="Twilio Auth Token.",
    )
    twilio_phone_number: str = Field(
        description="Twilio outbound caller phone number (E.164 format).",
    )

    # ------------------------------------------------------------------ #
    # Deepgram (Speech-to-Text)
    # ------------------------------------------------------------------ #
    deepgram_api_key: str | None = Field(
        default=None,
        description="Deepgram API key for transcription services.",
    )

    # ------------------------------------------------------------------ #
    # ElevenLabs (Text-to-Speech)
    # ------------------------------------------------------------------ #
    elevenlabs_api_key: str | None = Field(
        default=None,
        description="ElevenLabs API key for voice synthesis.",
    )

    # ------------------------------------------------------------------ #
    # Downstream service URLs
    # ------------------------------------------------------------------ #
    lead_service_url: str = Field(
        default="http://lead-service:8001",
        description="Internal URL for the Lead Management Service.",
    )
    voice_service_url: str = Field(
        default="http://voice-service:8002",
        description="Internal URL for the Voice/Telephony Service.",
    )

    # ------------------------------------------------------------------ #
    # Observability
    # ------------------------------------------------------------------ #
    sentry_dsn: str | None = Field(
        default=None,
        description="Sentry DSN for error tracking (leave blank to disable).",
    )
    environment: str = Field(
        default="development",
        description="Deployment environment: development | staging | production.",
    )

    # ------------------------------------------------------------------ #
    # Agent concurrency
    # ------------------------------------------------------------------ #
    max_concurrent_calls: int = Field(
        default=100,
        description="Maximum number of simultaneous outbound/inbound calls the agent handles.",
    )

    # ------------------------------------------------------------------ #
    # Kafka topic names
    # ------------------------------------------------------------------ #
    kafka_topic_lead_created: str = Field(
        default="lead.created",
        description="Topic consumed to trigger agent engagement on new leads.",
    )
    kafka_topic_conversation_completed: str = Field(
        default="conversation.completed",
        description="Topic published when a conversation ends.",
    )
    kafka_topic_call_initiated: str = Field(
        default="call.initiated",
        description="Topic published when an outbound call is triggered.",
    )
    kafka_topic_meeting_booked: str = Field(
        default="meeting.booked",
        description="Topic published when a meeting is successfully booked.",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# Singleton instance used across the application
settings = Settings()
