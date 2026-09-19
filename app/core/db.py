"""Engine, session factory and the declarative base.

One engine per process. FastAPI hands out a session per request through
`get_session`; the worker opens its own, because it outlives any request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings

# Explicit constraint naming, so Alembic can autogenerate a downgrade that
# actually drops the right index. Without it, unnamed constraints come out as
# random identifiers that differ between databases.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """created_at / updated_at, filled by the database rather than the app.

    Server-side defaults matter here: the worker and the API are separate
    processes, and a timestamp from whichever machine happened to write the row
    would make "what happened first" unanswerable.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,  # a connection idle overnight is dead by morning
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, rolled back on error."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
