"""Backfill CFTC TFF positions from yearly history files (idempotent upserts).

Run: docker compose exec app python -m scripts.backfill_cot_tff [--start-year 2010] [--end-year 2026]
"""

import argparse
import asyncio
from datetime import date

import httpx
from sqlalchemy import text

from app.db.session import session_scope
from app.services.cot_positions import contracts_by_code, download, load_cot_config, parse_tff_zip, upsert_cot_positions


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    args = parser.parse_args()

    config = load_cot_config()
    codes = contracts_by_code(config)
    start = date.fromisoformat(str(config["start_date"]))
    downloads = seen = written = 0
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        sources = [config["yearly_url"].format(year=y) for y in range(args.start_year, args.end_year + 1)]
        if args.start_year <= 2016:
            sources.insert(0, config["combined_url"])
        for url in sources:
            rows = parse_tff_zip(await download(client, url), codes, start=start)
            downloads += 1
            async with session_scope() as session:
                n = await upsert_cot_positions(session, rows)
            seen, written = seen + len(rows), written + n
            print(url.rsplit("/", 1)[1], "rows", len(rows), "written", n)
    async with session_scope() as session:
        summary = (await session.execute(text(
            "SELECT count(*), count(DISTINCT report_date), min(report_date), max(report_date) FROM cot_positions"
        ))).one()
    print({"downloads": downloads, "rows_seen": seen, "rows_written": written,
           "table_rows": summary[0], "weeks": summary[1], "first": str(summary[2]), "last": str(summary[3])})


if __name__ == "__main__":
    asyncio.run(main())
