"""Check EODHD GBOND yield access for the dashboard symbols."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from app.ingestion.eodhd_client import EODHDClient


SYMBOLS = (
    "US10Y.GBOND",
    "DE10Y.GBOND",
    "UK10Y.GBOND",
    "JP10Y.GBOND",
    "AU10Y.GBOND",
    "CA10Y.GBOND",
    "SW10Y.GBOND",
    "NZ10Y.GBOND",
)


async def main() -> None:
    start = date.today() - timedelta(days=14)
    end = date.today()
    async with EODHDClient() as client:
        for symbol in SYMBOLS:
            try:
                rows = await client.fetch_eod_history(
                    symbol,
                    from_date=start,
                    to_date=end,
                )
            except Exception as exc:  # noqa: BLE001 - diagnostic script.
                print(f"{symbol}: ERROR {type(exc).__name__}: {exc}")
                continue

            latest = rows[-1] if rows else None
            print(f"{symbol}: OK rows={len(rows)} latest={latest}")


if __name__ == "__main__":
    asyncio.run(main())
