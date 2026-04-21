"""Build central-bank policy bias and shift signals."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.policy_signals import PolicyBuildConfig, build_policy_signals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create policy stance and policy shift tables."
    )
    parser.add_argument(
        "--output-dir",
        default="data/policy",
        help="Directory for policy CSV/JSON exports. Defaults to data/policy.",
    )
    parser.add_argument("--inflation-weight", type=float, default=1.0)
    parser.add_argument("--labor-weight", type=float, default=1.0)
    parser.add_argument("--growth-weight", type=float, default=1.0)
    parser.add_argument("--strongly-hawkish-threshold", type=float, default=1.0)
    parser.add_argument("--mildly-hawkish-threshold", type=float, default=0.35)
    parser.add_argument("--mildly-dovish-threshold", type=float, default=-0.35)
    parser.add_argument("--strongly-dovish-threshold", type=float, default=-1.0)
    parser.add_argument("--flat-momentum-threshold", type=float, default=0.10)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    config = PolicyBuildConfig(
        inflation_weight=args.inflation_weight,
        labor_weight=args.labor_weight,
        growth_weight=args.growth_weight,
        strongly_hawkish_threshold=args.strongly_hawkish_threshold,
        mildly_hawkish_threshold=args.mildly_hawkish_threshold,
        mildly_dovish_threshold=args.mildly_dovish_threshold,
        strongly_dovish_threshold=args.strongly_dovish_threshold,
        flat_momentum_threshold=args.flat_momentum_threshold,
    )
    result = await build_policy_signals(Path(args.output_dir), config)
    print("Policy bias and shift signals built.")
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
