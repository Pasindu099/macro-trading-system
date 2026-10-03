"""Classify Step 4 Part C gaps: stored raw payload vs current live EODHD values.

Run: docker compose exec app python -m scripts.step5_rates_integrity
Uses one EODHD history call per symbol.
"""

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.eodhd_client import EODHDClient

SYMBOLS = ["GBPUSD.FOREX", "NZDUSD.FOREX", "AUDUSD.FOREX", "USDCHF.FOREX", "JP10Y.GBOND"]
TOLERANCE = {"FOREX": 0.0005, "GBOND": 0.01}  # relative % for FX, bp for yields


def _gap(symbol: str, a: float, b: float) -> float:
    return abs(a - b) / abs(b) * 100 if symbol.endswith(".FOREX") else abs(a - b) * 100


def classify(symbol: str, day: str, stored: float, live: dict[str, float]) -> str:
    """Return match, revision, date_shift or other for one stored row."""
    tol = TOLERANCE[symbol.rsplit(".", 1)[1]]
    if day in live and _gap(symbol, stored, live[day]) <= tol:
        return "match"
    current = date.fromisoformat(day)
    for neighbour in (current - timedelta(days=1), current + timedelta(days=1)):
        key = neighbour.isoformat()
        if key in live and _gap(symbol, stored, live[key]) <= tol:
            return "date_shift"
    return "revision" if day in live else "other"


async def main() -> None:
    snapshot = json.loads(Path("data/step4_rates_live_snapshot.json").read_text(encoding="utf-8"))
    start, end = date.fromisoformat(snapshot["from"]), date.fromisoformat(snapshot["to"])
    async with EODHDClient() as client:
        live = {
            symbol: {
                str(row["date"])[:10]: float(row["close"])
                for row in await client.fetch_eod_history(
                    symbol, from_date=start - timedelta(days=3), to_date=end + timedelta(days=3), period="d",
                )
                if row.get("close") is not None
            }
            for symbol in SYMBOLS
        }
    async with session_scope() as session:
        rows = []
        for symbol in SYMBOLS:
            fx = symbol.endswith(".FOREX")
            table = "fx_spot_observations" if fx else "government_yield_observations"
            column = "observation_date" if fx else "market_observation_date"
            result = await session.execute(text(f"""
                SELECT {column} AS d, (raw_payload->>'close')::float AS raw_close,
                       raw_payload->>'date' AS raw_date, ingested_at, is_outlier
                FROM {table} WHERE provider_symbol = :s AND {column} BETWEEN :a AND :b
                ORDER BY {column}, ingested_at
            """), {"s": symbol, "a": start, "b": end})
            rows += [(symbol, r) for r in result]
    summary: dict[str, dict[str, int]] = {}
    for symbol, row in rows:
        day = row.d.isoformat()
        verdict = classify(symbol, day, row.raw_close, live[symbol])
        summary.setdefault(symbol, {}).setdefault(verdict, 0)
        summary[symbol][verdict] += 1
        snap = snapshot["series"][symbol].get(day)
        if verdict != "match":
            print(symbol, day, "raw", row.raw_close, "raw_date", row.raw_date, "live_now", live[symbol].get(day),
                  "step4_snapshot", snap, "ingested", row.ingested_at.date(), verdict)
    for symbol in SYMBOLS:
        snap_days = set(snapshot["series"][symbol])
        snap_vs_live = sum(
            1 for d in snap_days
            if d in live[symbol] and _gap(symbol, snapshot["series"][symbol][d], live[symbol][d])
            > TOLERANCE[symbol.rsplit(".", 1)[1]]
        )
        print("SUMMARY", symbol, summary.get(symbol), "snapshot_vs_live_now_diffs", snap_vs_live)


if __name__ == "__main__":
    asyncio.run(main())
