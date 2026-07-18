import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from prometheus_fastapi_instrumentator import Instrumentator
import sentry_sdk

from app.config import get_settings
from app.database import engine
from app.routers import analytics
from app.kafka.consumer import AnalyticsConsumer

logger = logging.getLogger(__name__)
settings = get_settings()

analytics_consumer: AnalyticsConsumer | None = None
mongo_client: AsyncIOMotorClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global analytics_consumer, mongo_client
    
    logger.info("Starting Analytics Service...")
    
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)
        
    # MongoDB
    mongo_client = AsyncIOMotorClient(settings.mongodb_url)
    app.state.mongo_db = mongo_client.ai_platform
    
    # Start Kafka Consumer
    analytics_consumer = AnalyticsConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        redis_url=settings.redis_url,
    )
    await analytics_consumer.start()
    
    yield
    
    # Cleanup
    logger.info("Shutting down Analytics Service...")
    if analytics_consumer:
        await analytics_consumer.stop()
    if mongo_client:
        mongo_client.close()
    await engine.dispose()


app = FastAPI(
    title="AI Platform — Analytics Service",
    description="Calculates real-time business and conversational analytics",
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

app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["Analytics"])

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "analytics-service"
    }
