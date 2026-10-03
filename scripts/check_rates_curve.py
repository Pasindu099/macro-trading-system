"""Print compact curve availability for the configured benchmark countries."""

import asyncio

from app.services.curve_metrics import get_curve


async def main() -> None:
    for country in ("US", "DE", "UK", "JP", "AU", "NZ", "CA", "CH"):
        row = await get_curve(country)
        print(country, row["status"], row.get("as_of"),
              row.get("two_ten", {}).get("status"),
              row.get("ten_thirty", {}).get("status"),
              row.get("two_policy", {}).get("status"))


if __name__ == "__main__":
    asyncio.run(main())
