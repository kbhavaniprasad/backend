"""
app/database.py – Async SQLAlchemy engine, session factory, and Base model.

Uses asyncpg driver for high-throughput async database access.
"""

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# ── Engine ────────────────────────────────────────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_pre_ping=True,           # validate connections before checkout
    echo=settings.debug,          # log all SQL in debug mode
    future=True,
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,       # keep ORM objects usable after commit
    autocommit=False,
    autoflush=False,
)


# ── Declarative base ──────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models."""
    pass


# ── FastAPI dependency ────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide an async database session as a FastAPI dependency.

    Automatically rolls back on exception and always closes the session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Lifecycle helpers ─────────────────────────────────────────────────────────
async def init_db() -> None:
    """Create all tables (for development / testing; use Alembic in prod)."""
    async with engine.begin() as conn:
        from app.models import lead  # noqa: F401 – registers models with Base
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables verified/created.")


async def close_db() -> None:
    """Dispose the connection pool gracefully on shutdown."""
    await engine.dispose()
    logger.info("Database connection pool disposed.")
