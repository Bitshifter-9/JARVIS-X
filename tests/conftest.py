"""Shared test fixtures.

Integration tests run against a real PostgreSQL, never a SQLite stand-in: the queue's
correctness rests on ``FOR UPDATE SKIP LOCKED``, which SQLite does not have. A test that
cannot exercise the mechanism cannot prove it.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

# Point every import at the test database before application config is first read.
os.environ.setdefault(
    "JARVIS_DATABASE_URL", "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test"
)
os.environ.setdefault("JARVIS_ENV", "test")
os.environ.setdefault("JARVIS_JWT_SECRET", "test-secret-not-used-outside-tests")

import jarvis.db.models  # noqa: F401,E402  — registers models on Base.metadata
from jarvis.core.logging import configure_logging  # noqa: E402
from jarvis.db.base import Base  # noqa: E402
from jarvis.db.session import dispose_engine, get_engine, get_sessionmaker  # noqa: E402

configure_logging(level="WARNING", json_output=False)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
async def _clean_tables(_schema):
    """Truncate between tests.

    Not a per-test transaction: the concurrency tests need genuinely separate sessions
    committing against each other, which a shared outer transaction would serialize away.
    """
    table_names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with get_sessionmaker()() as session:
        await session.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
        await session.commit()
    yield


@pytest.fixture
async def session():
    async with get_sessionmaker()() as s:
        yield s
        await s.rollback()


@pytest.fixture
async def client():
    """An HTTP client bound to the ASGI app, with no network in between."""
    import httpx
    from jarvis.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
