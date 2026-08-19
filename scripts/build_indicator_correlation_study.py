"""Correlate a headline indicator against every other indicator for its country."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.indicator_correlation_study import build_indicator_correlation_study


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Same-month and +/-1-month-lag correlation of one indicator vs the rest."
    )
    parser.add_argument("--country", required=True, help="Two-letter country code, e.g. US")
    parser.add_argument(
        "--anchor",
        required=True,
        help="canonical_name of the headline indicator, e.g. ism_manufacturing_pmi",
    )
    parser.add_argument(
        "--output-dir",
        default="data/indicator_correlations",
        help="Directory for the CSV/JSON outputs. Defaults to data/indicator_correlations.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    summary = await build_indicator_correlation_study(
        country_code=args.country,
        anchor_indicator_key=args.anchor,
        output_dir=Path(args.output_dir),
    )
    print(f"Anchor: {summary['anchor_indicator_key']} ({summary['country_code']})")
    print(f"Observations: {summary['anchor_observation_count']} "
          f"({summary['anchor_period_start']} to {summary['anchor_period_end']})")
    print(f"Indicators compared: {summary['n_indicators_compared']}")
    print(f"CSV: {summary['csv_path']}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
