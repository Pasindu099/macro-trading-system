"""Build an EDA-ready macro dataset with pandas and SQLAlchemy."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.eda_dataset import build_eda_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create cleaned and normalized EDA macro tables and files."
    )
    parser.add_argument(
        "--output-dir",
        default="data/eda",
        help="Directory for EDA CSV/JSON outputs. Defaults to data/eda.",
    )
    parser.add_argument(
        "--scaling",
        choices=("both", "zscore", "minmax"),
        default="both",
        help="Normalization to expose as value_normalized. Defaults to z-score while storing both.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await build_eda_dataset(Path(args.output_dir), scaling=args.scaling)
    print("EDA macro dataset built.")
    print(f"Rows: {result['rows']}")
    print(f"Indicators: {result['indicators']}")
    print(f"Central banks: {', '.join(result['central_banks'])}")
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
