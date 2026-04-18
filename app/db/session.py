"""Async database engine and session management.

Everything database-related goes through functions in this module. Don't
instantiate engines or sessions directly elsewhere.

Usage patterns:

    # In FastAPI route handlers (via dependency injection):
    from fastapi import Depends
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.db.session import get_session

    @router.get("/items")
    async def list_items(session: AsyncSession = Depends(get_session)):
        ...

    # In scripts / background tasks:
    from app.db.session import session_scope

    async def do_work():
        async with session_scope() as session:
            # use session
            ...
        # Commit happens automatically on clean exit.
        # Rollback happens automatically on exception.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine.

    The engine manages the connection pool. One per process is correct —
    do NOT create multiple engines for the same DB URL.
    """
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        # Standard pool sizes for a small app. Tune later if needed.
        pool_size=5,
        max_overflow=10,
        # Recycle connections after 30 minutes to avoid stale connections.
        pool_recycle=1800,
        # Echo=True is useful locally for debugging SQL but very noisy.
        # Turn on via settings if ever needed, not globally.
        echo=False,
        # Hand out future-style 2.0 API.
        future=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory."""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,  # Lets us access attributes after commit.
        autoflush=False,          # Explicit flushes only — clearer semantics.
    )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a session, closes it after the request.

    Does NOT commit automatically. Route handlers should call
    `await session.commit()` explicitly when writes are intended.
    """
    maker = get_sessionmaker()
    async with maker() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for scripts and background tasks.

    - Commits on clean exit.
    - Rolls back on exception.
    - Always closes the session.

    Example:
        async with session_scope() as session:
            session.add(some_model)
            # commit happens on exit
    """
    maker = get_sessionmaker()
    session = maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    """Close all connections in the pool.

    Call this at process shutdown (e.g. from FastAPI's lifespan handler or
    at the end of a script) to ensure clean shutdown. Not strictly required
    but good hygiene.
    """
    engine = get_engine()
    await engine.dispose()