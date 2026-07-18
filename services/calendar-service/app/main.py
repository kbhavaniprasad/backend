import logging
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from aiokafka import AIOKafkaProducer
import sentry_sdk

from app.config import get_settings
from app.database import engine
from app.routers import calendar

logger = logging.getLogger(__name__)
settings = get_settings()

kafka_producer: AIOKafkaProducer | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global kafka_producer
    
    logger.info("Starting Calendar Service...")
    
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)
        
    # Start Kafka Producer
    kafka_producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
    )
    await kafka_producer.start()
    app.state.kafka_producer = kafka_producer
    
    yield
    
    # Cleanup
    logger.info("Shutting down Calendar Service...")
    if kafka_producer:
        await kafka_producer.stop()
    await engine.dispose()


app = FastAPI(
    title="AI Platform — Calendar Service",
    description="Manages Google and Outlook calendar integration and booking",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Instrumentator().instrument(app).expose(app)

app.include_router(calendar.router, prefix="/api/v1/calendar", tags=["Calendar"])

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "calendar-service"
    }
