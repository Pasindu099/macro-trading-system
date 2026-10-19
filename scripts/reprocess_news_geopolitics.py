"""Reclassify stored geopolitics after the no-FX-link rule change."""

from __future__ import annotations

import asyncio
from datetime import UTC

from sqlalchemy import text

from app.db.session import session_scope
from app.services.news_tagging import _episodes, classify, process_pending


async def main() -> None:
    episodes = await _episodes()
    reopened: list[int] = []
    updated = 0
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT r.id, r.title, r.body, r.source, r.source_category, r.published_at,
                   s.status, s.news_item_id
            FROM raw_news r JOIN news_raw_status s ON s.raw_news_id = r.id
            WHERE s.status IN ('kept', 'merged', 'dropped')
            ORDER BY r.id
        """))).mappings().all()
        for row in rows:
            result = classify(title=row["title"], summary=row["body"], source=row["source"],
                              source_category=row["source_category"],
                              published_at=row["published_at"].astimezone(UTC), episodes=episodes)
            if result.news_type != "Geopolitics":
                continue
            if row["status"] == "dropped" and result.status == "kept":
                await session.execute(text("DELETE FROM news_raw_status WHERE raw_news_id = :id"), {"id": row["id"]})
                reopened.append(row["id"])
            elif row["news_item_id"] is not None:
                await session.execute(text("""
                    UPDATE news_items SET no_fx_link = :flag, impact = :impact
                    WHERE id = :id
                """), {"flag": result.no_fx_link, "impact": result.impact, "id": row["news_item_id"]})
                updated += 1
    while reopened:
        await process_pending(limit=1000)
        async with session_scope() as session:
            pending = (await session.execute(text("""
                SELECT count(*) FROM raw_news r LEFT JOIN news_raw_status s ON s.raw_news_id = r.id
                WHERE r.id = ANY(:ids) AND s.raw_news_id IS NULL
            """), {"ids": reopened})).scalar_one()
        if pending == 0:
            break
        if pending == len(reopened):
            raise RuntimeError("Reprocessing did not advance; check news_tagging advisory lock")
    async with session_scope() as session:
        counts = (await session.execute(text("""
            SELECT status, count(*) FROM news_raw_status WHERE raw_news_id = ANY(:ids) GROUP BY status
        """), {"ids": reopened})).all() if reopened else []
    print({"reopened": len(reopened), "updated": updated,
           "new_kept_merged_dropped": {status: count for status, count in counts}})


if __name__ == "__main__":
    asyncio.run(main())
