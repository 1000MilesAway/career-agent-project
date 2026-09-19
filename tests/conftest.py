"""Shared fixtures for the product tests.

The database tests need a real Postgres: the schema uses JSONB and a partial
unique index, and SQLite would pass tests that Postgres would fail. When no
database answers, they skip rather than fail — a laptop without Docker should
still be able to run the rest of the suite.

No test here calls the LLM. Where a pipeline step would, it is monkeypatched.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.dbmark import DB_AVAILABLE, TEST_DATABASE_URL

ADMIN_URL = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
DB_NAME = TEST_DATABASE_URL.rsplit("/", 1)[1]


def _prepare_database() -> None:
    """Create the test database and its schema once, before any test runs.

    Done in a throwaway event loop of its own rather than in an async fixture:
    pytest-asyncio gives each test a fresh loop, and an asyncpg connection
    cannot cross loops — a session-scoped async engine fails with "attached to
    a different loop" on the second test that touches it.
    """

    async def build() -> None:
        admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{DB_NAME}"'))
        await admin.dispose()

        from app import models  # noqa: F401  registers the tables on Base.metadata
        from app.core.db import Base

        engine = create_async_engine(TEST_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(build())


if DB_AVAILABLE:
    # A dedicated database, never the development one: a test run must not be
    # able to delete rows someone paid for. The demo learned that with
    # PROBE_OUT and the product inherits the rule.
    _prepare_database()


@pytest_asyncio.fixture
async def engine():
    """A fresh engine per test, bound to this test's event loop."""
    test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture
async def clean_tables(engine):
    """Empty the tables after each test, so tests cannot see each other."""
    yield
    from app.core.db import Base

    async with engine.begin() as conn:
        tables = ", ".join(f'"{t}"' for t in Base.metadata.tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest_asyncio.fixture
async def session(engine, clean_tables) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def client(engine, clean_tables) -> AsyncIterator[AsyncClient]:
    """The API, wired to the test database."""
    from app.core.db import get_session
    from app.main import app

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _session_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def user(session):
    """An active account with a known password."""
    from app.core.security import hash_password
    from app.models import User

    account = User(
        email=f"u{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("correct horse"),
        is_active=True,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


@pytest_asyncio.fixture
async def auth_headers(client, user) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"email": user.email, "password": "correct horse"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
