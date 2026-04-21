"""Build rolling fundamental currency stance tables."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.currency_stance import (
    CurrencyStanceConfig,
    build_currency_stance_layer,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create rolling 1M/2M/3M currency stance tables."
    )
    parser.add_argument(
        "--output-dir",
        default="data/currency_stance",
        help="Directory for currency stance CSV/JSON exports.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await build_currency_stance_layer(
        Path(args.output_dir), CurrencyStanceConfig()
    )
    print("Currency stance layer built.")
    print(f"Output directory: {result['output_dir']}")
    print("Database tables:")
    for table in result["tables"]:
        print(f"  - {table}")
    print("Summary:")
    for key, value in result["summary"]["summary"].items():
        print(f"  {key}: {value}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
