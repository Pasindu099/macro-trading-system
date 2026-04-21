"""Fetch FX prices and validate policy signals against FX returns."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.fx_validation import FxValidationConfig, build_fx_validation_layer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Integrate FX history and validate policy signals."
    )
    parser.add_argument(
        "--output-dir",
        default="data/fx_validation",
        help="Directory for FX validation CSV/JSON/HTML exports.",
    )
    parser.add_argument("--start-date", type=parse_date, default=None)
    parser.add_argument("--end-date", type=parse_date, default=None)
    return parser.parse_args()


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


async def main() -> None:
    args = parse_args()
    config = FxValidationConfig(start_date=args.start_date, end_date=args.end_date)
    result = await build_fx_validation_layer(Path(args.output_dir), config)
    print("FX validation layer built.")
    print(f"Output directory: {result['output_dir']}")
    print("Database tables:")
    for table in result["tables"]:
        print(f"  - {table}")
    print("Summary:")
    for key, value in result["summary"]["summary"].items():
        print(f"  {key}: {value}")
    print(f"Recommendation: {result['summary']['recommendation']['status']}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
