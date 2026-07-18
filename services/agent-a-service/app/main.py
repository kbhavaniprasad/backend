"""
Agent A — Lead Engagement AI Service
=====================================
FastAPI application entry-point.

Startup sequence:
  1. Validate configuration (pydantic-settings).
  2. Connect to MongoDB via Motor.
  3. Connect to Redis.
  4. Initialise RAGEngine (Qdrant + OpenAI embeddings).
  5. Wire up the core service objects (PromptManager, handlers, producer).
  6. Start the Kafka consumer in a background asyncio task.
  7. Mount routers, Prometheus metrics, and Sentry.

Shutdown sequence:
  1. Cancel the Kafka consumer task.
  2. Stop the Kafka producer.
  3. Close Redis connection.
  4. Close Motor / MongoDB connection.
  5. Close RAGEngine (Qdrant client).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import redis.asyncio as aioredis
import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from openai import AsyncOpenAI
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import settings
from app.core.conversation_handler import ConversationHandler
from app.core.prompt_manager import PromptManager
from app.core.qualification_engine import LeadQualificationEngine
from app.core.rag_engine import RAGEngine
from app.kafka.consumer import LeadEventConsumer
from app.kafka.producer import EventProducer
from app.routers.agent import router as agent_router
from app.routers.conversations import router as conversations_router

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("agent_a_service")


# ─────────────────────────────────────────────────────────────────────────────
# Sentry (optional)
# ─────────────────────────────────────────────────────────────────────────────

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.2,
        profiles_sample_rate=0.1,
    )
    logger.info("Sentry initialised (env=%s).", settings.environment)


# ─────────────────────────────────────────────────────────────────────────────
# Application lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage all long-lived resources within the application lifespan.
    Everything attached to ``app.state`` is accessible from request handlers
    via ``request.app.state.<name>``.
    """
    logger.info("=== Agent A Service starting (env=%s) ===", settings.environment)

    # ── 1. MongoDB ───────────────────────────────────────────────────────
    mongo_client: AsyncIOMotorClient = AsyncIOMotorClient(settings.mongodb_url)  # type: ignore[type-arg]
    db = mongo_client[settings.mongodb_db_name]
    await _ensure_mongo_indexes(db)
    app.state.mongo_client = mongo_client
    app.state.db = db
    logger.info("MongoDB connected (db=%s).", settings.mongodb_db_name)

    # ── 2. Redis ─────────────────────────────────────────────────────────
    redis_client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    await redis_client.ping()
    app.state.redis = redis_client
    logger.info("Redis connected.")

    # ── 3. RAGEngine ─────────────────────────────────────────────────────
    rag_engine = RAGEngine(
        qdrant_url=settings.qdrant_url,
        openai_api_key=settings.openai_api_key,
        qdrant_api_key=settings.qdrant_api_key,
    )
    await rag_engine.initialize()
    app.state.rag_engine = rag_engine
    logger.info("RAGEngine initialised.")

    # ── 4. Core service objects ───────────────────────────────────────────
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    prompt_manager = PromptManager()
    qualification_engine = LeadQualificationEngine(
        openai_client=openai_client,
        model=settings.openai_model,
    )
    conversation_handler = ConversationHandler(
        prompt_manager=prompt_manager,
        rag_engine=rag_engine,
        qualification_engine=qualification_engine,
        openai_client=openai_client,
        model=settings.openai_model,
    )

    app.state.openai_client = openai_client
    app.state.prompt_manager = prompt_manager
    app.state.qualification_engine = qualification_engine
    app.state.conversation_handler = conversation_handler
    app.state.settings = settings
    logger.info("Core service objects wired.")

    # ── 5. Kafka producer ─────────────────────────────────────────────────
    producer = EventProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
    await producer.start()
    app.state.producer = producer
    logger.info("Kafka producer started.")

    # ── 6. HTTP client (for outbound calls to downstream services) ────────
    http_client = httpx.AsyncClient(timeout=15.0)
    app.state.http_client = http_client

    # ── 7. Kafka consumer (background task) ───────────────────────────────
    consumer = LeadEventConsumer(producer=producer, redis_client=redis_client)
    consumer_task = asyncio.create_task(consumer.start(), name="kafka-consumer")
    app.state.consumer = consumer
    app.state.consumer_task = consumer_task
    logger.info("Kafka consumer task started.")

    logger.info("=== Agent A Service ready ===")

    # ── Yield — application is now running ────────────────────────────────
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("=== Agent A Service shutting down… ===")

    # Cancel Kafka consumer
    consumer_task.cancel()
    try:
        await asyncio.wait_for(consumer_task, timeout=10)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    logger.info("Kafka consumer stopped.")

    # Stop Kafka producer
    await producer.stop()
    logger.info("Kafka producer stopped.")

    # Close HTTP client
    await http_client.aclose()

    # Close Redis
    await redis_client.aclose()
    logger.info("Redis connection closed.")

    # Close RAGEngine
    await rag_engine.close()
    logger.info("RAGEngine closed.")

    # Close MongoDB
    mongo_client.close()
    logger.info("MongoDB connection closed.")

    logger.info("=== Agent A Service shutdown complete ===")


# ─────────────────────────────────────────────────────────────────────────────
# MongoDB index bootstrap
# ─────────────────────────────────────────────────────────────────────────────

async def _ensure_mongo_indexes(db) -> None:  # type: ignore[type-arg]
    """Create compound indexes on the conversations collection."""
    from pymongo import ASCENDING, DESCENDING

    coll = db.conversations
    await coll.create_index([("id", ASCENDING)], unique=True, background=True)
    await coll.create_index(
        [("tenant_id", ASCENDING), ("created_at", DESCENDING)], background=True
    )
    await coll.create_index(
        [("tenant_id", ASCENDING), ("lead_id", ASCENDING)], background=True
    )
    await coll.create_index(
        [("tenant_id", ASCENDING), ("status", ASCENDING)], background=True
    )
    logger.info("MongoDB indexes ensured.")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Agent A — Lead Engagement Service",
    description=(
        "AI-powered lead engagement agent that autonomously conducts conversations "
        "across voice, WhatsApp, SMS, chat, and Instagram channels.  "
        "Includes RAG-powered knowledge retrieval, BANT lead qualification, "
        "meeting booking detection, and full conversation persistence."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.environment == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Prometheus metrics ────────────────────────────────────────────────────────
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(conversations_router)
app.include_router(agent_router)


# ─────────────────────────────────────────────────────────────────────────────
# Health endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"], include_in_schema=True)
async def health(request: Request) -> JSONResponse:
    """
    Lightweight health check used by Kubernetes liveness / readiness probes.

    Verifies connectivity to MongoDB and Redis.  Returns HTTP 200 when healthy,
    HTTP 503 when any dependency is unavailable.
    """
    checks: dict[str, str] = {}

    # MongoDB
    try:
        await request.app.state.db.command("ping")
        checks["mongodb"] = "ok"
    except Exception as exc:
        logger.warning("Health check: MongoDB unavailable — %s", exc)
        checks["mongodb"] = f"error: {exc}"

    # Redis
    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("Health check: Redis unavailable — %s", exc)
        checks["redis"] = f"error: {exc}"

    # Kafka consumer task
    consumer_task: asyncio.Task = request.app.state.consumer_task
    checks["kafka_consumer"] = "running" if not consumer_task.done() else "stopped"

    healthy = all(v in {"ok", "running"} for v in checks.values())

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "degraded",
            "service": "agent-a-service",
            "version": "1.0.0",
            "environment": settings.environment,
            "checks": checks,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Global exception handler
# ─────────────────────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler that prevents raw tracebacks leaking to clients."""
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred.  Please try again later.",
            "path": str(request.url.path),
        },
    )
