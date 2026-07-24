"""
main.py — Application entry point
Starts FastAPI, wires up middleware, initialises the database, and runs Uvicorn.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import database as db
from api import router
from config import config

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("server.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Voice Agent API starting...")
    await db.init_db()
    logger.info("✅ Ready on http://localhost:%s", config.PORT)
    yield
    logger.info("👋 Shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Voice Agent API",
    description="Lightweight Retell AI voice agent backend",
    version="1.0.0",
    lifespan=lifespan,
    # Hide stack traces from API responses
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
)

# CORS — allow the frontend dev server and any configured origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routes
app.include_router(router)


@app.get("/health")
async def root_health():
    """Root health check — useful for load balancers."""
    return {"status": "ok", "service": "voice-agent-api"}


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=True,        # auto-reload on file changes
        log_level="info",
    )
