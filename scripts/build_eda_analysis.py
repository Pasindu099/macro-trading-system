"""Generate exploratory analysis reports from the EDA macro dataset."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.eda_analysis import build_eda_analysis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create descriptive, time-series, correlation, Granger, PCA, and visual EDA outputs."
    )
    parser.add_argument(
        "--output-dir",
        default="data/eda/analysis",
        help="Directory for EDA analysis outputs. Defaults to data/eda/analysis.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await build_eda_analysis(Path(args.output_dir))
    print("EDA analysis built.")
    print(f"Rows analyzed: {result['rows']}")
    print(f"Series analyzed: {result['series']}")
    print(f"Correlation pairs: {result['correlation_pairs']}")
    print(f"Lag pairs: {result['lag_pairs']}")
    print(f"Granger tests: {result['granger_tests']}")
    print(f"Output directory: {result['output_dir']}")
    print("Files:")
    for file_name in result["files"]:
        print(f"  - {file_name}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
