"""Check EODHD symbol availability for Step 4 missing rates and FX data."""

import asyncio
from pathlib import Path

import yaml

from app.ingestion.eodhd_client import EODHDClient
from app.services.fx_spot import FX_PAIR_SYMBOLS
from app.services.rates import _fetch_gbond_symbol_set, _gbond_symbol_code


async def main() -> None:
    bonds = await _fetch_gbond_symbol_set()
    prefixes = {"US": "US", "DE": "DE", "FR": "FR", "GB": "UK", "JP": "JP",
                "AU": "AU", "NZ": "NZ", "CA": "CA", "CH": "SW"}
    for country, prefix in prefixes.items():
        print(country, *(f"{tenor}:{'yes' if prefix + tenor in bonds else 'no'}" for tenor in ("2Y", "10Y", "20Y", "30Y")))
    async with EODHDClient() as client:
        rows = await client.fetch_exchange_symbols("FOREX")
    forex = {_gbond_symbol_code(row) for row in rows if isinstance(row, dict)}
    configured = yaml.safe_load(Path("config/pairs.yaml").read_text(encoding="utf-8"))["pairs"]
    missing = [item["pair"] for item in configured if item["pair"] not in FX_PAIR_SYMBOLS]
    print("forex_symbols", len(forex), "missing_pairs", len(missing))
    for pair in missing:
        symbol = pair.replace("/", "")
        print(pair, "yes" if symbol in forex else "no")


if __name__ == "__main__":
    asyncio.run(main())
