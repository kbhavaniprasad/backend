"""
Agent B — AI Manager Service
FastAPI application entry point.

Responsibilities:
  - Consume 'conversation.completed' Kafka events
  - Evaluate Agent A conversations using GPT-4o (TranscriptAnalyzer)
  - Generate and apply learnings (LearningGenerator)
  - Maintain knowledge base quality (KnowledgeUpdater)
  - Expose REST API for evaluation reports, learnings, and business reports
  - Emit Prometheus metrics and integrate with Sentry
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import get_settings
from app.core.knowledge_updater import KnowledgeUpdater
from app.core.learning_generator import LearningGenerator
from app.core.performance_evaluator import PerformanceEvaluator
from app.core.transcript_analyzer import TranscriptAnalyzer
from app.kafka.consumer import EvaluationConsumer
from app.kafka.producer import EvaluationProducer
from app.routers import evaluations as evaluations_router
from app.routers import learnings as learnings_router
from app.routers import reports as reports_router

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

settings = get_settings()

# ---------------------------------------------------------------------------
# Sentry initialisation (no-op if DSN is empty)
# ---------------------------------------------------------------------------

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
    )
    logger.info("Sentry initialised for environment: %s", settings.environment)


# ---------------------------------------------------------------------------
# Application lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application-wide resources:
      - MongoDB Motor client
      - Kafka producer
      - Core service instances
      - Kafka consumer (background task)
    """
    logger.info("Starting Agent B service…")

    # ── MongoDB ──────────────────────────────────────────────────────────
    mongo_client = AsyncIOMotorClient(settings.mongodb_url)
    db = mongo_client[settings.mongodb_db_name]
    app.state.db = db

    # Create indexes for common queries
    try:
        await db["evaluation_reports"].create_index(
            [("tenant_id", 1), ("created_at", -1)]
        )
        await db["evaluation_reports"].create_index([("conversation_id", 1)])
        await db["learnings"].create_index(
            [("tenant_id", 1), ("status", 1), ("created_at", -1)]
        )
        await db["learnings"].create_index([("source_evaluation_id", 1)])
        await db["embedding_update_log"].create_index(
            [("tenant_id", 1), ("created_at", -1)]
        )
        logger.info("MongoDB indexes ensured")
    except Exception as exc:
        logger.warning("Could not create indexes: %s", exc)

    # ── Kafka producer ────────────────────────────────────────────────────
    producer = EvaluationProducer()
    try:
        await producer.start()
        app.state.producer = producer
    except Exception as exc:
        logger.error("Kafka producer failed to start: %s", exc)
        app.state.producer = producer  # Still attach; publish calls will log errors

    # ── Core services ─────────────────────────────────────────────────────
    transcript_analyzer = TranscriptAnalyzer()
    evaluator = PerformanceEvaluator(
        db=db,
        kafka_producer=producer,
        transcript_analyzer=transcript_analyzer,
    )
    learning_gen = LearningGenerator(db=db, kafka_producer=producer)
    knowledge_updater = KnowledgeUpdater(db=db)

    app.state.evaluator = evaluator
    app.state.learning_generator = learning_gen
    app.state.knowledge_updater = knowledge_updater

    # ── Kafka consumer (background task) ─────────────────────────────────
    consumer = EvaluationConsumer(
        performance_evaluator=evaluator,
        learning_generator=learning_gen,
        knowledge_updater=knowledge_updater,
    )
    consumer_task = asyncio.create_task(consumer.start(), name="kafka-consumer")
    app.state.consumer = consumer
    logger.info("Kafka consumer task started")

    logger.info(
        "Agent B service ready — environment: %s", settings.environment
    )

    yield  # ── Application running ──────────────────────────────────────

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("Shutting down Agent B service…")

    await consumer.stop()
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    await producer.stop()
    mongo_client.close()
    logger.info("Agent B service shutdown complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Agent B — AI Manager Service",
    description=(
        "AI Manager Agent that evaluates Agent A's conversation performance, "
        "generates actionable learnings, applies corrections, and drives "
        "continuous improvement of the lead engagement AI."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production via env var
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", tags=["Observability"])

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(evaluations_router.router)
app.include_router(learnings_router.router)
app.include_router(reports_router.router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    tags=["Observability"],
    summary="Service health check",
    response_description="Returns service status and dependency health",
)
async def health_check(request: Request) -> JSONResponse:
    """
    Lightweight health check endpoint.
    Verifies MongoDB connectivity and returns service metadata.
    """
    db_status = "ok"
    try:
        await request.app.state.db.command("ping")
    except Exception as exc:
        logger.error("MongoDB ping failed: %s", exc)
        db_status = f"error: {exc}"

    healthy = db_status == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "service": settings.service_name,
            "status": "healthy" if healthy else "degraded",
            "environment": settings.environment,
            "version": "1.0.0",
            "dependencies": {
                "mongodb": db_status,
            },
        },
    )


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler that logs unhandled exceptions and returns 500."""
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
            "detail": "An internal server error occurred.",
            "path": str(request.url.path),
        },
    )
