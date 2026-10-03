"""Read-only inventory of mapped indicators with null start date and period."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import dispose_engine, session_scope


SQL = text("""
WITH targets AS (
    SELECT DISTINCT indicator_id
    FROM indicator_releases
    WHERE indicator_id IS NOT NULL AND actual IS NOT NULL
      AND period_start_date IS NULL AND period IS NULL
)
SELECT i.id, i.country_code, i.canonical_name, i.frequency,
       count(*) AS total_rows,
       count(*) FILTER (WHERE r.is_latest) AS latest_rows,
       count(*) FILTER (WHERE r.period_start_date IS NULL AND r.period IS NULL)
           AS null_period_rows,
       count(*) FILTER (WHERE r.period_start_date IS NULL AND r.period IS NULL
                        AND r.is_latest) AS null_period_latest_rows,
       count(DISTINCT r.released_at::date) FILTER (
           WHERE r.period_start_date IS NULL AND r.period IS NULL
       ) AS null_period_release_dates
FROM targets t
JOIN indicators i ON i.id = t.indicator_id
JOIN indicator_releases r ON r.indicator_id = i.id
GROUP BY i.id, i.country_code, i.canonical_name, i.frequency
ORDER BY i.country_code, i.canonical_name
""")


async def main() -> None:
    try:
        async with session_scope() as session:
            rows = (await session.execute(SQL)).mappings().all()
        print("| Country | Indicator | Frequency | Total rows | Latest rows | Null-period rows | Null-period latest | Distinct release dates |")
        print("|---|---|---|---:|---:|---:|---:|---:|")
        for row in rows:
            print("| " + " | ".join(str(row[key]) for key in (
                "country_code", "canonical_name", "frequency", "total_rows",
                "latest_rows", "null_period_rows", "null_period_latest_rows",
                "null_period_release_dates",
            )) + " |")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
