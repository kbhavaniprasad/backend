"""
tests/conftest.py – Shared pytest fixtures for lead-service tests.

Uses an in-memory SQLite database (via aiosqlite) so tests run without
an external PostgreSQL instance.  Kafka is mocked to avoid needing a broker.
"""

import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base, get_db
from app.main import create_app

# ── In-memory async SQLite ────────────────────────────────────────────────────
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    """Create all tables once per test session."""
    async with test_engine.begin() as conn:
        # Import models so they register with Base
        from app.models import lead  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture()
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional test session that always rolls back."""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture()
def mock_kafka():
    """Return a mock KafkaProducerService."""
    mock = MagicMock()
    mock.publish_lead_created = AsyncMock()
    mock.publish_lead_status_changed = AsyncMock()
    mock.publish_lead_assigned = AsyncMock()
    return mock


@pytest_asyncio.fixture()
async def client(
    db_session: AsyncSession,
    mock_kafka,
) -> AsyncGenerator[AsyncClient, None]:
    """
    Return an httpx AsyncClient wired to the test FastAPI app.

    Overrides the DB dependency and injects the mock Kafka producer.
    """
    app: FastAPI = create_app()

    # Override DB dependency
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # Inject mock Kafka into app state
    app.state.kafka_producer = mock_kafka

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "X-Tenant-ID": str(uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
            "X-User-ID": str(uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")),
        },
    ) as ac:
        yield ac


@pytest.fixture()
def tenant_id() -> uuid.UUID:
    return uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture()
def user_id() -> uuid.UUID:
    return uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
