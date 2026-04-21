"""Build the post-collection processed macro dataset."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.macro_dataset import build_processed_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create processed macro tables and data quality reports."
    )
    parser.add_argument(
        "--output-dir",
        default="data/processed",
        help="Directory for CSV/JSON processed outputs. Defaults to data/processed.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await build_processed_dataset(Path(args.output_dir))
    print("Processed macro dataset built.")
    print(f"Output directory: {result['output_dir']}")
    print("Database tables:")
    for table in result["tables"]:
        print(f"  - {table}")
    print("Files:")
    for file_name in result["files"]:
        print(f"  - {file_name}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
