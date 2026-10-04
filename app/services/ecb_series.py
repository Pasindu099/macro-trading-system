"""ECB Data Portal CSV observations stored by series key."""

from __future__ import annotations

import csv
from datetime import date
from io import StringIO

import httpx
from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger

API = "https://data-api.ecb.europa.eu/service/data"
HICP_SA = "HICP.M.U2.Y.000000.4F0.INX"


def parse_series_csv(payload: str, series_key: str) -> list[tuple[date, float]]:
    points = []
    for row in csv.DictReader(StringIO(payload.lstrip("\ufeff"))):
        if row.get("KEY") != series_key or not row.get("OBS_VALUE"):
            continue
        stamp = row["TIME_PERIOD"]
        day = date.fromisoformat(stamp + "-01" if len(stamp) == 7 else stamp)
        value = float(row["OBS_VALUE"])
        if value <= 0:
            raise ValueError(f"Nonpositive ECB index value for {series_key} on {day}")
        points.append((day, value))
    if not points:
        raise ValueError(f"No observations for {series_key}")
    return points


async def ingest_series(series_key: str, start: date = date(2019, 1, 1)) -> int:
    dataset, suffix = series_key.split(".", 1)
    async with run_logger("job:ecb_series") as run, httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        response = await client.get(f"{API}/{dataset}/{suffix}",
                                    params={"format": "csvdata", "startPeriod": start.isoformat()},
                                    headers={"Accept": "text/csv"})
        response.raise_for_status()
        points = parse_series_csv(response.text, series_key)
        async with session_scope() as session:
            await session.execute(text("""INSERT INTO ecb_series_observations
                (series_key, observation_date, value) VALUES (:key, :day, :value)
                ON CONFLICT (series_key, observation_date) DO UPDATE SET
                value = EXCLUDED.value, retrieved_at = now()"""),
                [{"key": series_key, "day": day, "value": value} for day, value in points])
        run.record_rows(len(points))
    return len(points)


async def load_series(series_key: str, start: date | None = None) -> list[tuple[date, float]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""SELECT observation_date, value::float AS value
            FROM ecb_series_observations WHERE series_key = :key
              AND (CAST(:start AS date) IS NULL OR observation_date >= :start)
            ORDER BY observation_date"""), {"key": series_key, "start": start})
        return [(row.observation_date, row.value) for row in rows]
