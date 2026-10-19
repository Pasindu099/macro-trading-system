"""Last-30-day investingLive data-headline match and discrepancy counts."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import session_scope


async def main() -> None:
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT a.status, count(*) AS n
            FROM newswire_actuals a JOIN news_items n ON n.id = a.news_item_id
            WHERE n.source = 'investinglive' AND n.news_type = 'Data'
              AND n.published_at >= now() - interval '30 days'
            GROUP BY a.status
        """))).all()
    counts = {status: n for status, n in rows}
    parsed = sum(n for status, n in counts.items() if status != "unparsed")
    matched = sum(counts.get(status, 0) for status in ("newswire", "confirmed", "discrepancy"))
    print({"last_30d": counts, "parsed": parsed, "matched": matched,
           "match_rate": round(matched / parsed, 3) if parsed else None})


if __name__ == "__main__":
    asyncio.run(main())
