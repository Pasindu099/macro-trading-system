"""Audit economic indicator freshness and coverage.

Usage:
    python scripts/audit_economic_coverage.py
    python scripts/audit_economic_coverage.py --country CA --limit 80
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.db.session import session_scope


FRESHNESS_DAYS = {
    "daily": 7,
    "weekly": 21,
    "monthly": 55,
    "quarterly": 125,
    "irregular": 125,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", help="Optional country code, e.g. US, CA, EU")
    parser.add_argument("--limit", type=int, default=200)
    return parser.parse_args()


def row_dict(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


async def main_async(args: argparse.Namespace) -> int:
    country_filter = ""
    params: dict[str, Any] = {"limit": args.limit}
    if args.country:
        country_filter = "where i.country_code = :country"
        params["country"] = args.country.upper()

    async with session_scope() as session:
        summary = (
            await session.execute(
                text(
                    """
                    select i.country_code,
                           count(distinct i.id) as indicators,
                           count(r.id) as releases,
                           min(r.released_at) filter (where r.actual is not null) as first_actual,
                           max(r.released_at) filter (where r.actual is not null) as latest_actual,
                           count(distinct i.id) filter (where r.id is null) as empty_indicators
                    from indicators i
                    left join indicator_releases r
                      on r.indicator_id = i.id
                     and r.actual is not null
                    group by i.country_code
                    order by i.country_code
                    """
                )
            )
        ).all()

        stale = (
            await session.execute(
                text(
                    f"""
                    with latest as (
                        select i.country_code,
                               i.canonical_name,
                               i.display_name,
                               i.frequency,
                               i.importance,
                               count(r.id) as rows,
                               min(r.released_at) filter (where r.actual is not null) as first_actual,
                               max(r.released_at) filter (where r.actual is not null) as latest_actual
                        from indicators i
                        left join indicator_releases r
                          on r.indicator_id = i.id
                         and r.actual is not null
                        {country_filter}
                        group by i.country_code, i.canonical_name, i.display_name,
                                 i.frequency, i.importance
                    )
                    select *
                    from latest
                    where rows = 0
                       or latest_actual < now() - (
                            case frequency
                                when 'daily' then interval '7 days'
                                when 'weekly' then interval '21 days'
                                when 'monthly' then interval '55 days'
                                when 'quarterly' then interval '125 days'
                                else interval '125 days'
                            end
                       )
                    order by country_code, importance, latest_actual nulls first, canonical_name
                    limit :limit
                    """
                ),
                params,
            )
        ).all()

        unmapped = (
            await session.execute(
                text(
                    """
                    select raw_payload->>'country' as country,
                           raw_payload->>'type' as type,
                           coalesce(raw_payload->>'comparison', 'null') as comparison,
                           count(*) as releases,
                           max(released_at) as latest
                    from indicator_releases
                    where indicator_id is null
                    group by 1, 2, 3
                    order by releases desc, latest desc
                    limit :limit
                    """
                ),
                {"limit": min(args.limit, 100)},
            )
        ).all()

    print(f"Economic coverage audit generated_at={datetime.now(UTC).isoformat()}")
    print("\nSUMMARY")
    for row in summary:
        print(row_dict(row))

    print("\nSTALE_OR_EMPTY")
    for row in stale:
        print(row_dict(row))

    print("\nTOP_UNMAPPED")
    for row in unmapped:
        print(row_dict(row))

    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
