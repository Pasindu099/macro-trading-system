"""Backfill Step 13 F assets and print per-series coverage."""

import asyncio
from datetime import date

from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.eodhd_client import EODHDClient
from app.services.dxy import DXY_WEIGHTS, build_computed_dxy
from app.services.fx_spot import ingest_eodhd_fx_spot
from app.services.news_assets import FRED_ASSETS, FX_ASSETS, METALS, backfill_snapshot, link_news_episodes


async def main() -> None:
    links = await link_news_episodes()
    print(f"episode_links_added={links}")
    async with EODHDClient() as client:
        for pair in DXY_WEIGHTS:
            async with session_scope() as session:
                stats = await ingest_eodhd_fx_spot(
                    session, client, from_date=date(2000, 1, 1), to_date=date.today(),
                    pairs=[pair], max_requests=1,
                )
                print(f"fx_{pair}: inserted={stats.observations_inserted} errors={len(stats.errors)}")
    async with session_scope() as session:
        print(f"dxy_inserted={await build_computed_dxy(session)}")
    counts = await backfill_snapshot()
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT series_id, min(observation_date) AS first_day, max(observation_date) AS last_day,
                   count(*) AS days FROM cross_asset_daily_snapshots GROUP BY series_id
        """))).mappings().all()
    coverage = {row["series_id"]: row for row in rows}
    for series in sorted(set(FRED_ASSETS.values()) | set(FX_ASSETS.values()) | set(METALS.values())):
        row = coverage.get(series)
        print(f"{series}: {row['days']} {row['first_day']}..{row['last_day']}" if row else f"{series}: 0 unavailable")
    print(f"total_observations={sum(counts.values())}")


if __name__ == "__main__":
    asyncio.run(main())
