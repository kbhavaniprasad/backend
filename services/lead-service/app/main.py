"""
app/main.py – Lead Service FastAPI application entry point.

Responsibilities
----------------
- Construct and configure the FastAPI application.
- Register lifespan events: init Kafka producer, DB pool, Sentry, Prometheus.
- Mount routers: leads, webhooks.
- Expose /health and /metrics endpoints.
- Apply global exception handlers and CORS middleware.
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import sentry_sdk
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.config import get_settings
from app.database import close_db, init_db
from app.kafka.producer import KafkaProducerService
from app.routers.leads import router as leads_router
from app.routers.webhooks import router as webhooks_router

# ── Logging ───────────────────────────────────────────────────────────────────

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger(__name__)


# ── Sentry initialisation ─────────────────────────────────────────────────────

def _init_sentry() -> None:
    """Initialise Sentry error tracking if a DSN is configured."""
    if not settings.sentry_dsn:
        logger.info("Sentry DSN not configured – skipping initialisation.")
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
        send_default_pii=False,
    )
    logger.info("Sentry initialised. environment=%s", settings.environment)


# ── Application lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage the application lifecycle.

    Startup
    -------
    1. Initialise Sentry.
    2. Verify / create database tables (dev only; prod uses Alembic).
    3. Start the Kafka producer and attach it to ``app.state``.

    Shutdown
    --------
    1. Stop the Kafka producer (flush pending messages).
    2. Dispose the SQLAlchemy connection pool.
    """
    logger.info("=== Lead Service starting up. environment=%s ===", settings.environment)

    # 1. Sentry
    _init_sentry()

    # 2. Database
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Database init warning (non-fatal): %s", exc)

    # 3. Kafka producer
    kafka_producer = KafkaProducerService(
        bootstrap_servers=settings.kafka_bootstrap_servers
    )
    try:
        await kafka_producer.start()
        app.state.kafka_producer = kafka_producer
        logger.info("Kafka producer attached to app state.")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to start Kafka producer (continuing without it): %s", exc
        )
        app.state.kafka_producer = None

    logger.info("=== Lead Service startup complete. ===")

    yield  # Application is running

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("=== Lead Service shutting down. ===")

    if app.state.kafka_producer is not None:
        await app.state.kafka_producer.stop()

    await close_db()

    logger.info("=== Lead Service shutdown complete. ===")


# ── FastAPI application factory ────────────────────────────────────────────────

def create_app() -> FastAPI:
    """
    Construct and return the configured FastAPI application.

    Separated into a factory function so tests can create isolated instances.
    """
    application = FastAPI(
        title="AI Platform Lead Service",
        description=(
            "Microservice responsible for ingesting, managing, and tracking "
            "leads across all acquisition channels (Facebook, Google, LinkedIn, "
            "WhatsApp, website forms, and CRM integrations)."
        ),
        version="1.0.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url="/redoc" if settings.environment != "production" else None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    # In production, restrict origins to the API gateway domain.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Prometheus metrics ────────────────────────────────────────────────────
    Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        should_respect_env_var=True,
        env_var_name="ENABLE_METRICS",
        excluded_handlers=["/health", "/metrics"],
    ).instrument(application).expose(application, include_in_schema=False)

    # ── Routers ───────────────────────────────────────────────────────────────
    application.include_router(leads_router)
    application.include_router(webhooks_router)

    # ── Global exception handlers ─────────────────────────────────────────────
    @application.exception_handler(Exception)
    async def global_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "Unhandled exception on %s %s", request.method, request.url
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "An unexpected error occurred. Please try again later.",
                "path": str(request.url),
            },
        )

    # ── Request timing middleware ─────────────────────────────────────────────
    @application.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
        return response

    # ── Health endpoint ───────────────────────────────────────────────────────
    @application.get(
        "/health",
        tags=["Health"],
        summary="Service liveness and readiness check",
        include_in_schema=True,
    )
    async def health_check(request: Request) -> JSONResponse:
        """
        Return the service health status.

        Checks
        ------
        - Kafka producer: is it connected and ready?
        - Overall status: ``healthy`` | ``degraded``

        This endpoint is polled by Kubernetes liveness and readiness probes.
        """
        kafka_ok = (
            request.app.state.kafka_producer is not None
            and request.app.state.kafka_producer._producer is not None
        )

        overall = "healthy" if kafka_ok else "degraded"
        http_status = (
            status.HTTP_200_OK if overall == "healthy"
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )

        return JSONResponse(
            status_code=http_status,
            content={
                "status": overall,
                "service": "lead-service",
                "version": "1.0.0",
                "environment": settings.environment,
                "checks": {
                    "kafka_producer": "ok" if kafka_ok else "unavailable",
                },
            },
        )

    return application


# ── Module-level app instance (used by uvicorn) ───────────────────────────────

app = create_app()
