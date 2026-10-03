"""Verify sourced curve, FR-DE and newly added FX driver availability."""

import asyncio

from app.services.curve_metrics import get_curve
from app.services.rates_drivers import get_pair_drivers
from app.services.yield_spreads import get_spread


NEW_PAIRS = (
    "EUR/AUD", "EUR/NZD", "EUR/CAD", "GBP/AUD", "GBP/NZD", "GBP/CAD", "GBP/CHF",
    "AUD/CAD", "AUD/CHF", "NZD/CAD", "NZD/CHF", "NZD/JPY", "CAD/CHF", "CHF/JPY",
)


async def main() -> None:
    curves = {country: (await get_curve(country))["ten_thirty"]["status"]
              for country in ("US", "DE", "UK", "JP", "AU", "NZ", "CA", "CH")}
    fr_de = {tenor: (await get_spread("FR-DE", tenor))["status"] for tenor in ("2Y", "10Y", "30Y")}
    drivers = {}
    for tenor in ("2Y", "10Y"):
        drivers[tenor] = 0
        for pair in NEW_PAIRS:
            drivers[tenor] += (await get_pair_drivers(pair, tenor))["status"] != "unavailable"
    print({"10s30s": curves, "FR-DE": fr_de, "new_pair_drivers_available": drivers})


if __name__ == "__main__":
    asyncio.run(main())
