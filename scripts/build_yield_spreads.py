"""Rebuild derived yield spreads from stored observations."""

import asyncio

from sqlalchemy import text

from app.db.session import session_scope
from app.services.yield_spreads import build_yield_spreads


async def main() -> None:
    async with session_scope(statement_timeout="15min") as session:
        count = await build_yield_spreads(session)
        result = await session.execute(text("""
            SELECT tenor, COUNT(DISTINCT spread_name) AS names
            FROM yield_spreads GROUP BY tenor ORDER BY tenor
        """))
        coverage = {row.tenor: row.names for row in result}
    print({"yield_spreads": count, "names_by_tenor": coverage})


if __name__ == "__main__":
    asyncio.run(main())
