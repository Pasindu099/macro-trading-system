"""Load annual Eurostat government balance as percent of GDP for EZ, DE and FR."""

import argparse
import asyncio

import httpx
from sqlalchemy import text

from app.db.session import get_sessionmaker

URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/gov_10dd_edpt1"
PARAMS = [
    ("freq", "A"), ("unit", "PC_GDP"), ("sector", "S13"), ("na_item", "B9"),
    ("geo", "EA20"), ("geo", "DE"), ("geo", "FR"), ("sinceTimePeriod", "2020"),
]
COUNTRIES = {"EA20": "EZ", "DE": "DE", "FR": "FR"}


def parse_observations(payload: dict) -> list[dict]:
    dimensions = payload["id"]
    sizes = payload["size"]
    categories = {key: payload["dimension"][key]["category"]["index"] for key in dimensions}
    if any(isinstance(index, list) for index in categories.values()):
        raise ValueError("Unexpected Eurostat category index")
    records = []
    for geo, country in COUNTRIES.items():
        if geo not in categories["geo"]:
            continue
        for year, year_index in categories["time"].items():
            offset = 0
            for dimension, size in zip(dimensions, sizes):
                offset = offset * size + (categories[dimension][geo] if dimension == "geo"
                                         else year_index if dimension == "time" else 0)
            value = payload["value"].get(str(offset))
            if value is not None:
                records.append({"country_code": country, "year": int(year), "balance_pct_gdp": value})
    return records


async def main(dry_run: bool) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(URL, params=PARAMS)
        response.raise_for_status()
    rows = parse_observations(response.json())
    if not dry_run and rows:
        async with get_sessionmaker()() as session:
            await session.execute(text("""
                INSERT INTO country_fiscal_observations (country_code, year, balance_pct_gdp)
                VALUES (:country_code, :year, :balance_pct_gdp)
                ON CONFLICT (country_code, year) DO UPDATE
                  SET balance_pct_gdp = EXCLUDED.balance_pct_gdp, ingested_at = now()
            """), rows)
            await session.commit()
    print({"dry_run": dry_run, "rows": len(rows), "countries": sorted({r["country_code"] for r in rows}),
           "latest_year": max((r["year"] for r in rows), default=None)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
