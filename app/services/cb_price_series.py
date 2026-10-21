"""Official monthly CPI index levels for non-US/ECB CB tracking."""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime

import httpx

from app.db.session import session_scope
from app.services.fred import load_series, store_observations

SOURCES = {
    "BOE": ("CB_UK_CPI", "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7bt/mm23/data"),
    "BOC": ("CB_CA_CPI", "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"),
    "SNB": ("CB_CH_CPI", "https://data.snb.ch/api/cube/plkopr/data/json/en?fromDate=2024-01"),
    "BOJ": ("CB_JP_CORE_CPI", "https://www.e-stat.go.jp/en/stat-search/file-download?fileKind=1&statInfId=000040482943"),
}
logger = logging.getLogger(__name__)


def parse_levels(bank: str, response: httpx.Response) -> list[tuple[date, float]]:
    if bank == "BOE":
        rows = [(datetime.strptime(row["date"], "%Y %b").date(), float(row["value"]))
                for row in response.json()["months"]]
    elif bank == "BOC":
        points = response.json()[0]["object"]["vectorDataPoint"]
        rows = [(date.fromisoformat(row["refPer"]), float(row["value"])) for row in points if row["value"] is not None]
    elif bank == "SNB":
        series = next(s for s in response.json()["timeseries"] if "LD2010100" in s["metadata"]["key"])
        rows = [(date.fromisoformat(row["date"] + "-01"), float(row["value"])) for row in series["values"]]
    else:
        lines = csv.reader(io.StringIO(response.content.decode("cp932")))
        rows = [(date(int(row[0][:4]), int(row[0][4:6]), 1), float(row[2])) for row in lines
                if row and len(row[0]) == 6 and row[0].isdigit() and row[2] not in ("", "-")]
    return [(day, value) for day, value in rows if day >= date(2024, 1, 1)]


async def ingest_official_cpi() -> dict[str, int]:
    counts: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for bank, (series_id, url) in SOURCES.items():
            try:
                response = await (client.post(url, json=[{"vectorId": 41690973, "latestN": 40}]) if bank == "BOC"
                                  else client.get(url))
                response.raise_for_status()
                points = parse_levels(bank, response)
                async with session_scope() as session:
                    counts[bank] = await store_observations(session, series_id, [
                        {"observation_date": day, "value": value, "realtime_start": None,
                         "raw": {"source": url, "date": day.isoformat(), "value": value}}
                        for day, value in points])
            except (httpx.HTTPError, ValueError, KeyError, IndexError, StopIteration) as exc:
                logger.warning("Official CPI index refresh failed for %s: %s", bank, exc)
                counts[bank] = 0
    return counts


async def load_levels(bank: str, start: date) -> list[tuple[date, float]]:
    return await load_series(SOURCES[bank][0], start)
