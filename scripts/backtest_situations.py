"""Backtest configured situations over all available stored history."""

import asyncio
import json
from pathlib import Path

from app.services.situations import backtest_all


async def main() -> None:
    results = await backtest_all()
    path = Path("data/situations_backtest.json")
    path.write_text(json.dumps(results, default=str, indent=2), encoding="utf-8")
    print({"scopes": len(results), "episodes": sum(len(row["episodes"]) for row in results),
           "active": sum(row["status"] == "active" for row in results),
           "unavailable": sum(row["status"] == "unavailable" for row in results),
           "output": str(path)})


if __name__ == "__main__":
    asyncio.run(main())
