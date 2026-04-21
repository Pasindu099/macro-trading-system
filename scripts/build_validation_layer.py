"""Build historical validation metrics for macro and policy signals."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.validation import ValidationConfig, build_validation_layer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate macro indices and policy signals against future outcomes."
    )
    parser.add_argument(
        "--output-dir",
        default="data/validation",
        help="Directory for validation CSV/JSON/HTML exports.",
    )
    parser.add_argument("--minimum-pairs", type=int, default=12)
    parser.add_argument("--rolling-window", type=int, default=20)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    config = ValidationConfig(
        minimum_pairs=args.minimum_pairs,
        rolling_window=args.rolling_window,
    )
    result = await build_validation_layer(Path(args.output_dir), config)
    print("Historical validation layer built.")
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
