"""Print compact driver availability across configured G10 pairs."""

import asyncio
from pathlib import Path

import yaml

from app.services.rates_drivers import get_pair_drivers


async def main() -> None:
    pairs = yaml.safe_load(Path("config/pairs.yaml").read_text(encoding="utf-8"))["pairs"]
    counts = {}
    for pair in pairs:
        result = await get_pair_drivers(pair["pair"], "2Y")
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    print(counts)


if __name__ == "__main__":
    asyncio.run(main())
