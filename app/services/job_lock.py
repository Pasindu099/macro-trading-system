"""Connection-scoped PostgreSQL locks for analytics jobs.

The connection stays checked out while a job runs. A transaction-level lock
would be released at each builder's commit, so it cannot protect a pipeline.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.db.session import get_engine


@asynccontextmanager
async def job_lock(name: str) -> AsyncIterator[bool]:
    """Yield whether the named lock was acquired, releasing it on exit."""
    async with get_engine().connect() as connection:
        acquired = bool((await connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": name}
        )).scalar_one())
        try:
            yield acquired
        finally:
            if acquired:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:name))"),
                    {"name": name},
                )
