"""Read-only count of print identities changed by the proposed dedup key.

Run before changing event_innovation._RELEASES_SQL:
    python -m scripts.diff_event_innovation_keys
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import dispose_engine, session_scope


_KEY_DIFF_SQL = text(
    """
    WITH releases AS (
        SELECT indicator_id, period_start_date, period, released_at,
               COALESCE(period_start_date::text, released_at::date::text) AS old_key,
               COALESCE(period_start_date::text, period, released_at::date::text) AS new_key
        FROM indicator_releases
        WHERE indicator_id IS NOT NULL AND actual IS NOT NULL
    ), changed AS (
        SELECT * FROM releases WHERE old_key IS DISTINCT FROM new_key
    ), old_groups AS (
        SELECT indicator_id, old_key FROM releases GROUP BY indicator_id, old_key
    ), new_groups AS (
        SELECT indicator_id, new_key FROM releases GROUP BY indicator_id, new_key
    )
    SELECT (SELECT count(*) FROM changed) AS changed_rows,
           (SELECT count(DISTINCT indicator_id) FROM changed) AS changed_indicators,
           (SELECT count(*) FROM old_groups) AS old_prints,
           (SELECT count(*) FROM new_groups) AS new_prints,
           (SELECT count(*) FROM changed WHERE period_start_date IS NOT NULL)
               AS changed_with_start_date,
           (SELECT count(*) FROM changed WHERE period IS NULL)
               AS changed_with_null_period,
           (SELECT count(*) FROM changed WHERE period_start_date IS NULL AND period IS NOT NULL)
               AS changed_with_period_fallback
    """
)


async def main() -> None:
    try:
        async with session_scope() as session:
            row = (await session.execute(_KEY_DIFF_SQL)).mappings().one()
            for key, value in row.items():
                print(f"{key}: {value}")
            print(f"net_print_count_change: {row['new_prints'] - row['old_prints']}")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
