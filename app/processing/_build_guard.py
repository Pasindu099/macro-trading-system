"""Fail a transactional macro rebuild before an empty output can commit."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def require_rows(session: AsyncSession, table: str) -> None:
    # Callers supply fixed table names from code, never user input.
    present = (await session.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})"))).scalar_one()
    if not present:
        raise RuntimeError(f"Macro rebuild produced no rows in {table}")
