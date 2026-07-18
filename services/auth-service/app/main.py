"""
main.py — FastAPI application entry point for the AI Platform Auth Service.

Responsibilities
----------------
- Construct and configure the FastAPI application instance.
- Register middleware: CORS, Sentry, OpenTelemetry, Prometheus.
- Mount routers: auth, users.
- Define the lifespan context manager that initialises the database on startup.
- Expose the /health liveness probe endpoint.
"""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from prometheus_fastapi_instrumentator import Instrumentator
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.config import get_settings
from app.database import create_all_tables
from app.routers.auth import router as auth_router
from app.routers.users import router as users_router

# --------------------------------------------------------------------------- #
# Logging configuration                                                        #
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# OpenTelemetry setup                                                          #
# --------------------------------------------------------------------------- #

def _configure_tracing(service_name: str) -> None:
    """
    Initialise an OpenTelemetry ``TracerProvider`` with a ``ConsoleSpanExporter``.

    In production, swap ``ConsoleSpanExporter`` for an OTLP exporter pointing
    at your collector (e.g. Jaeger, Honeycomb, Grafana Tempo).
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry tracing initialised for service '%s'.", service_name)


# --------------------------------------------------------------------------- #
# Sentry setup                                                                 #
# --------------------------------------------------------------------------- #

def _configure_sentry(dsn: str, environment: str) -> None:
    """Initialise Sentry error tracking if a DSN is provided."""
    if not dsn:
        logger.info("Sentry DSN not configured — error tracking is disabled.")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.2,   # Capture 20% of transactions as traces
        send_default_pii=False,   # Never send personally identifiable information
    )
    logger.info(
        "Sentry initialised (environment=%s, traces_sample_rate=0.2).", environment
    )


# --------------------------------------------------------------------------- #
# Lifespan                                                                     #
# --------------------------------------------------------------------------- #

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan context manager.

    Startup
    -------
    - Read settings (validates all required env vars early).
    - Configure Sentry and OpenTelemetry.
    - Create any missing database tables (idempotent bootstrap).

    Shutdown
    --------
    - Flush pending telemetry / Sentry events (handled by SDK atexit hooks).
    """
    settings = get_settings()

    # Observability
    _configure_sentry(settings.sentry_dsn, settings.environment)
    _configure_tracing("auth-service")

    # Database bootstrap
    logger.info("Running database table creation (if needed)…")
    await create_all_tables()
    logger.info("Database ready.")

    logger.info(
        "Auth Service starting up (environment=%s).", settings.environment
    )
    yield  # Application is running

    logger.info("Auth Service shutting down.")


# --------------------------------------------------------------------------- #
# Application factory                                                          #
# --------------------------------------------------------------------------- #

def create_app() -> FastAPI:
    """
    Construct and return the configured FastAPI application.

    Separated from module-level instantiation so that tests can call
    ``create_app()`` with different settings without global side-effects.
    """
    settings = get_settings()

    application = FastAPI(
        title="AI Platform Auth Service",
        description=(
            "Handles tenant registration, user authentication (local + Google OAuth2), "
            "JWT issuance / refresh / revocation, and RBAC user management."
        ),
        version="1.0.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url="/redoc" if settings.environment != "production" else None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------ #
    # CORS                                                                 #
    # ------------------------------------------------------------------ #
    # Tighten ``allow_origins`` in production to your exact front-end URL.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment != "production" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ #
    # Routers                                                              #
    # ------------------------------------------------------------------ #
    application.include_router(auth_router)
    application.include_router(users_router)

    # ------------------------------------------------------------------ #
    # Prometheus metrics                                                   #
    # Exposes /metrics for Prometheus scraping                             #
    # ------------------------------------------------------------------ #
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=False,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(application).expose(application, endpoint="/metrics", tags=["Observability"])

    # ------------------------------------------------------------------ #
    # OpenTelemetry instrumentation                                        #
    # ------------------------------------------------------------------ #
    FastAPIInstrumentor.instrument_app(
        application,
        excluded_urls="health,metrics",
    )

    return application


# --------------------------------------------------------------------------- #
# Application instance (used by uvicorn)                                      #
# --------------------------------------------------------------------------- #

app: FastAPI = create_app()


# --------------------------------------------------------------------------- #
# Health probe                                                                 #
# --------------------------------------------------------------------------- #

@app.get(
    "/health",
    tags=["Observability"],
    summary="Liveness probe",
    response_description="Service status.",
)
async def health_check() -> dict[str, str]:
    """
    Lightweight liveness probe.

    Returns ``{"status": "ok", "service": "auth-service"}`` when the process
    is running.  Kubernetes / Docker health checks should target this endpoint.
    """
    return {"status": "ok", "service": "auth-service"}
