"""
database.py — Async SQLAlchemy engine, session factory, and base model.

Usage
-----
- Import `Base` and inherit from it in every ORM model.
- Use `get_db()` as a FastAPI dependency to obtain a scoped AsyncSession.
- Call `create_all_tables()` during application lifespan startup.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from app.config import get_settings

# --------------------------------------------------------------------------- #
# Engine                                                                       #
# --------------------------------------------------------------------------- #

def _build_engine() -> AsyncEngine:
    """Construct the async engine with production-ready pool settings."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,                # Set True only for debugging SQL
        pool_size=20,              # Persistent connections kept in pool
        max_overflow=40,           # Extra connections allowed beyond pool_size
        pool_pre_ping=True,        # Verify connections before use
        pool_recycle=3600,         # Recycle connections after 1 hour
    )


engine: AsyncEngine = _build_engine()

# --------------------------------------------------------------------------- #
# Session factory                                                              #
# --------------------------------------------------------------------------- #

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Keep attributes accessible after commit
    autoflush=False,
    autocommit=False,
)

# --------------------------------------------------------------------------- #
# Declarative base                                                             #
# --------------------------------------------------------------------------- #

Base = declarative_base()

# --------------------------------------------------------------------------- #
# Helper utilities                                                             #
# --------------------------------------------------------------------------- #

async def create_all_tables() -> None:
    """
    Create all ORM-mapped tables that do not yet exist in the database.

    Called during application lifespan startup so the schema is always
    in sync with the models.  In production you should prefer Alembic
    migrations; this function acts as a safety net / bootstrap helper.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a scoped ``AsyncSession``.

    The session is automatically closed (and rolled back on error)
    when the request context exits.

    Example
    -------
    ::

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()
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
