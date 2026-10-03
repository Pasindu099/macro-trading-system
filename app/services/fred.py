"""FRED series ingestion (fred_observations) for CB tracking and the regime model."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger
from app.services.government_yields import hash_payload
from app.settings import get_settings

logger = logging.getLogger(__name__)

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
# PCE price indexes (levels), unemployment rate, real GDP (level, SAAR), ICE BofA US HY OAS (percent).
TRACKING_SERIES = ("PCEPI", "PCEPILFE", "UNRATE", "GDPC1", "BAMLH0A0HYM2",
                   "DCOILBRENTEU", "VIXCLS", "SP500")
INCREMENTAL_LOOKBACK = timedelta(days=730)  # re-fetch two years so revisions land


def parse_observations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """FRED marks missing values with '.'; those rows are skipped."""
    rows = []
    for obs in payload.get("observations", []):
        if obs.get("value") in (None, "", "."):
            continue
        rows.append({
            "observation_date": date.fromisoformat(obs["date"]),
            "value": float(obs["value"]),
            "realtime_start": date.fromisoformat(obs["realtime_start"]) if obs.get("realtime_start") else None,
            "raw": obs,
        })
    return rows


async def fetch_series(client: httpx.AsyncClient, series_id: str, start: date) -> list[dict[str, Any]]:
    response = await client.get(FRED_OBSERVATIONS_URL, params={
        "series_id": series_id, "api_key": get_settings().fred_api_key, "file_type": "json",
        "observation_start": start.isoformat(),
    })
    response.raise_for_status()
    return parse_observations(response.json())


async def store_observations(session: AsyncSession, series_id: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    count_sql = text("SELECT count(*) FROM fred_observations WHERE series_id = :s")
    before = (await session.execute(count_sql, {"s": series_id})).scalar_one()
    await session.execute(text("""
        INSERT INTO fred_observations (series_id, observation_date, value, realtime_start, payload_hash, raw_payload)
        VALUES (:series_id, :observation_date, :value, :realtime_start, :payload_hash, CAST(:raw AS jsonb))
        ON CONFLICT ON CONSTRAINT uq_fred_series_date_hash DO NOTHING
    """), [{
        "series_id": series_id, "observation_date": r["observation_date"], "value": r["value"],
        "realtime_start": r["realtime_start"], "payload_hash": hash_payload({"series": series_id, **r["raw"]}),
        "raw": json.dumps(r["raw"]),
    } for r in rows])
    # executemany reports rowcount -1 on asyncpg, so count the rows actually added.
    return (await session.execute(count_sql, {"s": series_id})).scalar_one() - before


async def ingest_fred_series(series: tuple[str, ...] = TRACKING_SERIES, start: date | None = None) -> dict[str, int]:
    """job:fred_series — fetch each series from `start` (default: two years back) and store new values."""
    if not get_settings().fred_api_key:
        raise RuntimeError("FRED_API_KEY is not configured")
    start = start or date.today() - INCREMENTAL_LOOKBACK
    counts: dict[str, int] = {}
    async with run_logger("job:fred_series") as run, httpx.AsyncClient(timeout=60) as client:
        for series_id in series:
            try:
                rows = await fetch_series(client, series_id, start)
            except httpx.HTTPError as exc:
                run.errors.append(f"{series_id}: {exc}")
                continue
            async with session_scope() as session:
                counts[series_id] = await store_observations(session, series_id, rows)
        run.record_rows(sum(counts.values()))
    return counts


async def load_series(series_id: str, start: date | None = None) -> list[tuple[date, float]]:
    """Newest value per observation date."""
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT DISTINCT ON (observation_date) observation_date, value::float AS value
            FROM fred_observations
            WHERE series_id = :s AND (CAST(:start AS date) IS NULL OR observation_date >= :start)
            ORDER BY observation_date, ingested_at DESC, id DESC
        """), {"s": series_id, "start": start})
        return [(r.observation_date, r.value) for r in rows]
