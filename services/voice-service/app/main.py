"""
Voice Service — FastAPI Application Entry Point
================================================
Powered by Retell AI for intelligent voice agent calls.

Architecture:
  ┌──────────────────────────────────────────────────────┐
  │               Retell AI Platform                      │
  │  Phone Call → STT → LLM (GPT-4o) → TTS → Audio     │
  └──────────────────┬───────────────────────────────────┘
                     │ Webhooks (call_started/ended/analyzed)
  ┌──────────────────▼───────────────────────────────────┐
  │               Voice Service (FastAPI)                 │
  │  /webhooks/retell  →  orchestrator  →  Kafka events  │
  └──────────────────────────────────────────────────────┘
"""

import logging
from contextlib import asynccontextmanager

import aioredis
import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import get_settings
from app.kafka.consumer import LeadEventConsumer
from app.kafka.producer import KafkaProducerService
from app.retell.client import RetellClient
from app.routers import calls, webhooks, phone_numbers

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Global service instances ──────────────────────────────────────────────────
retell_client: RetellClient | None = None
kafka_producer: KafkaProducerService | None = None
kafka_consumer: LeadEventConsumer | None = None
redis_client: aioredis.Redis | None = None
mongo_client: AsyncIOMotorClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle manager."""
    global retell_client, kafka_producer, kafka_consumer, redis_client, mongo_client

    logger.info("🚀 Starting Voice Service (Retell AI)")

    # Sentry
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)

    # MongoDB
    mongo_client = AsyncIOMotorClient(settings.mongodb_url)
    app.state.db = mongo_client.ai_platform

    # Ensure indexes
    await app.state.db.calls.create_index("call_id", unique=True)
    await app.state.db.calls.create_index("lead_id")
    await app.state.db.calls.create_index("tenant_id")
    await app.state.db.calls.create_index("created_at")

    # Redis
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis_client

    # Retell AI Client
    retell_client = RetellClient(settings)
    app.state.retell = retell_client

    # Kafka Producer
    kafka_producer = KafkaProducerService(settings.kafka_bootstrap_servers)
    await kafka_producer.start()
    app.state.kafka_producer = kafka_producer

    # Kafka Consumer (consumes 'lead.created' → triggers Retell calls)
    kafka_consumer = LeadEventConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        retell_client=retell_client,
        redis=redis_client,
        db=app.state.db,
        kafka_producer=kafka_producer,
        settings=settings,
    )
    await kafka_consumer.start()

    logger.info("✅ Voice Service ready — Retell Agent ID: %s", settings.retell_agent_id)

    yield

    # Shutdown
    logger.info("Shutting down Voice Service...")
    if kafka_consumer:
        await kafka_consumer.stop()
    if kafka_producer:
        await kafka_producer.stop()
    if retell_client:
        await retell_client.close()
    if redis_client:
        await redis_client.close()
    if mongo_client:
        mongo_client.close()


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Platform — Voice Service",
    description="AI voice call management powered by Retell AI",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["Webhooks"])
app.include_router(calls.router, prefix="/api/v1/calls", tags=["Calls"])
app.include_router(phone_numbers.router, prefix="/api/v1/phone-numbers", tags=["Phone Numbers"])


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "ok",
        "service": "voice-service",
        "retell_agent_id": settings.retell_agent_id,
        "provider": "retell-ai",
    }
