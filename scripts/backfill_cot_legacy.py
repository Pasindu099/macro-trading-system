"""Detached/idempotent official Legacy backfill; print final counts only."""
import asyncio
import json
from app.db.session import dispose_engine, session_scope
from app.services.cot_legacy import ingest
from sqlalchemy import text


async def main():
    result = await ingest()
    async with session_scope() as session:
        rows = (await session.execute(text("""SELECT currency,count(*) AS rows,min(report_date) AS first,max(report_date) AS last
            FROM cot_legacy_positions GROUP BY currency ORDER BY currency"""))).mappings().all()
    print(json.dumps({**result,"coverage":[dict(row) for row in rows]}, default=str))
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
