"""The `requires_db` marker, in its own module so tests can import it plainly.

`tests/` is not a package (there is no __init__.py, and adding one would change
how the existing suite is collected), so `from .conftest import ...` does not
work. A sibling module does.
"""

from __future__ import annotations

import asyncio
import os

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://career:career@localhost:5433/career_test"
)


def _database_available() -> bool:
    """Whether a Postgres we may create a test database on is reachable."""
    from sqlalchemy.ext.asyncio import create_async_engine

    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"

    async def check() -> bool:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect():
                return True
        except Exception:
            return False
        finally:
            await engine.dispose()

    try:
        return asyncio.run(check())
    except Exception:
        return False


DB_AVAILABLE = _database_available()

requires_db = pytest.mark.skipif(
    not DB_AVAILABLE, reason="Postgres недоступен — тесты БД пропущены"
)
