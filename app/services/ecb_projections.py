"""Structured ECB Macroeconomic Projection Database point projections."""

from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from typing import Any

import httpx
from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger

BANK = "ECB"
API = "https://data-api.ecb.europa.eu/service/data/MPD"
ITEMS = {"HIC": ("hicp_inflation", "A", -5, 25),
         "HEF": ("core_hicp_inflation", "A", -5, 25),
         "YER": ("real_gdp", "A", -20, 20),
         "URX": ("unemployment_rate", "F", 0, 30)}
SEASONS = {"W": 3, "G": 6, "S": 9, "A": 12}


def round_date(code: str) -> date:
    """Month marker for an MPD exercise; the feed has no exact publication date."""
    return date(2000 + int(code[1:]), SEASONS[code[0]], 1)


def parse_mpd_csv(payload: str, code: str) -> list[dict[str, Any]]:
    marker = round_date(code)
    out = []
    for row in csv.DictReader(StringIO(payload.lstrip("\ufeff"))):
        item = row.get("PD_ITEM")
        if (item not in ITEMS or row.get("FREQ") != "A" or row.get("REF_AREA") != "U2"
                or row.get("PD_SEAS_EX") != code or row.get("PD_ORIGIN") != "0000"):
            continue
        variable, denominator, lower, upper = ITEMS[item]
        if row.get("SERIES_DENOM") != denominator or not row.get("OBS_VALUE"):
            continue
        year = int(row["TIME_PERIOD"])
        if not marker.year <= year <= marker.year + 4:
            continue
        value = float(row["OBS_VALUE"])
        if not lower <= value <= upper:
            raise ValueError(f"Implausible {variable} {year} in {code}: {value}")
        out.append({"bank": BANK, "release_date": marker, "variable": variable,
                    "horizon": str(year), "stat": "median", "value": value})
    if {row["variable"] for row in out} != {v[0] for v in ITEMS.values()}:
        raise ValueError(f"Incomplete ECB MPD round {code}")
    return out


async def load_ecb_projections(since: date = date(2020, 1, 1)) -> dict[str, Any]:
    """Idempotently load every published quarter from the ECB SDMX CSV API."""
    summary: dict[str, Any] = {"loaded": {}, "unavailable": {}}
    async with run_logger("job:ecb_projections") as run, httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for year in range(since.year, date.today().year + 1):
            for season, month in SEASONS.items():
                marker = date(year, month, 1)
                if marker < since or marker > date.today():
                    continue
                code = f"{season}{year % 100:02d}"
                try:
                    response = await client.get(f"{API}/A.U2.HIC+HEF+YER+URX..{code}.0000",
                                                params={"format": "csvdata"}, headers={"Accept": "text/csv"})
                    if response.status_code == 404:
                        summary["unavailable"][code] = "Not published in MPD"
                        continue
                    response.raise_for_status()
                    rows = parse_mpd_csv(response.text, code)
                except (httpx.HTTPError, ValueError) as exc:
                    summary["unavailable"][code] = str(exc)
                    run.errors.append(f"{code}: {exc}")
                    continue
                async with session_scope() as session:
                    await session.execute(text("DELETE FROM cb_projection_values WHERE bank = :b AND release_date = :d"),
                                          {"b": BANK, "d": marker})
                    await session.execute(text("""INSERT INTO cb_projection_values
                        (bank, release_date, variable, horizon, stat, value)
                        VALUES (:bank, :release_date, :variable, :horizon, :stat, :value)"""), rows)
                summary["loaded"][code] = len(rows)
                run.record_rows(len(rows))
    return summary


async def get_projections(round_: str = "latest") -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        rounds = (await session.execute(text("SELECT DISTINCT release_date FROM cb_projection_values "
                                             "WHERE bank = 'ECB' ORDER BY release_date"))).scalars().all()
        if not rounds:
            return {"bank": BANK, "status": "unavailable", "reason": "No ECB projections stored"}
        wanted = rounds if round_ == "all" else [rounds[-1]] if round_ == "latest" else [date.fromisoformat(round_)]
        rows = await session.execute(text("""SELECT release_date, variable, horizon, stat, value::float AS value
            FROM cb_projection_values WHERE bank = 'ECB' AND release_date = ANY(:dates)
            ORDER BY release_date, variable, horizon"""), {"dates": wanted})
        grouped: dict[str, dict] = {}
        for row in rows:
            grouped.setdefault(row.release_date.isoformat(), {}).setdefault(row.variable, {}).setdefault(row.horizon, {})[row.stat] = row.value
    return {"bank": BANK, "round_date_method": "first day of MPD exercise month", "stat_method": "point projection",
            "rounds": [{"release_date": day, "variables": variables} for day, variables in grouped.items()]}
