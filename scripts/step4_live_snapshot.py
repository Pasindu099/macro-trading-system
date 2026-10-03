"""Capture a fixed EODHD window before Part C switches rates reads to the DB."""

import asyncio
import json
from datetime import date
from pathlib import Path

from app.ingestion.eodhd_client import EODHDClient
from app.services.rates import FX_PAIR_DEFS, YIELD_BENCHMARKS


FROM_DATE = date(2026, 8, 3)
TO_DATE = date(2026, 8, 21)
OUTPUT = Path("data/step4_rates_live_snapshot.json")


async def main() -> None:
    symbols = [str(row["symbol"]) for row in (*YIELD_BENCHMARKS, *FX_PAIR_DEFS)]
    async with EODHDClient() as client:
        histories = await asyncio.gather(
            *(client.fetch_eod_history(symbol, from_date=FROM_DATE, to_date=TO_DATE) for symbol in symbols),
            return_exceptions=True,
        )
    snapshot = {
        "from": FROM_DATE.isoformat(),
        "to": TO_DATE.isoformat(),
        "series": {
            symbol: (
                {str(row["date"]): float(row["close"]) for row in history if row.get("date") and row.get("close") is not None}
                if isinstance(history, list) else {"error": str(history)}
            )
            for symbol, history in zip(symbols, histories, strict=True)
        },
    }
    OUTPUT.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print({symbol: len(values) if "error" not in values else "error" for symbol, values in snapshot["series"].items()})


if __name__ == "__main__":
    asyncio.run(main())
