"""Build the macro feature engineering and indicator mapping layer."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.macro_features import build_feature_layer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create macro feature engineering tables and reports."
    )
    parser.add_argument(
        "--output-dir",
        default="data/features",
        help="Directory for feature CSV/JSON exports. Defaults to data/features.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await build_feature_layer(Path(args.output_dir))
    print("Macro feature layer built.")
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
