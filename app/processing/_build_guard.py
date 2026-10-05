"""Fail a transactional macro rebuild before an empty output can commit."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope


@asynccontextmanager
async def build_session(
    shared_session: AsyncSession | None, *, statement_timeout: str | None = None,
) -> AsyncIterator[AsyncSession]:
    """Reuse the chain transaction, or own one for a standalone build."""
    if shared_session is not None:
        yield shared_session
    else:
        async with session_scope(statement_timeout=statement_timeout) as session:
            yield session


async def require_rows(session: AsyncSession, table: str) -> None:
    # Callers supply fixed table names from code, never user input.
    present = (await session.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})"))).scalar_one()
    if not present:
        raise RuntimeError(f"Macro rebuild produced no rows in {table}")
