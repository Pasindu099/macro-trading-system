"""Build macro pressure weights and theme-level country indices."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.macro_indices import IndexBuildConfig, build_macro_indices


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create macro pressure index tables and reports."
    )
    parser.add_argument(
        "--output-dir",
        default="data/indices",
        help="Directory for index CSV/JSON exports. Defaults to data/indices.",
    )
    parser.add_argument("--min-sample-size", type=int, default=24)
    parser.add_argument("--min-abs-correlation", type=float, default=0.40)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    config = IndexBuildConfig(
        min_sample_size=args.min_sample_size,
        min_abs_correlation=args.min_abs_correlation,
    )
    result = await build_macro_indices(Path(args.output_dir), config)
    print("Macro pressure indices built.")
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
