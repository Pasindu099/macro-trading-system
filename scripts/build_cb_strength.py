"""Build CB reaction function strength scores.

Run after build_macro_indices.py (requires processed.theme_indices).

Usage:
    python -m scripts.build_cb_strength
    python -m scripts.build_cb_strength --output-dir data/cb_strength
    python -m scripts.build_cb_strength --windows 1 2 3 6
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.cb_reaction_score import (
    CBStrengthConfig,
    DEFAULT_CB_WEIGHTS,
    build_cb_strength_score,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build CB mandate-weighted currency strength scores."
    )
    parser.add_argument(
        "--output-dir",
        default="data/cb_strength",
        help="Directory for CSV/JSON exports (default: data/cb_strength).",
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=[1, 2, 3],
        metavar="N",
        help="Rolling window lengths in months (default: 1 2 3).",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    config = CBStrengthConfig(windows_months=tuple(args.windows))

    print(f"Building CB strength scores (windows: {config.windows_months})…")
    result = await build_cb_strength_score(
        Path(args.output_dir), config, DEFAULT_CB_WEIGHTS
    )

    print(f"\nOutput → {result['output_dir']}")
    print("Tables written:")
    for t in result["tables"]:
        print(f"  {t}")

    s = result["summary"]
    print(f"\nRows:       scores={s['summary'].get('score_rows')}  "
          f"rankings={s['summary'].get('ranking_rows')}")
    print(f"Date range: {s['summary'].get('first_date')} → {s['summary'].get('last_date')}")
    print(f"Currencies: {s['summary'].get('currencies')}")

    print("\nLatest rankings (2-month window):")
    for row in s.get("latest_rankings", []):
        if row["window_months"] == 2:
            print(
                f"  #{row['rank_strongest']}  {row['currency']:3s}  "
                f"score={row['cb_strength_score']:+.3f}  "
                f"{row['strength_label']:12s}  {row['trend_label']}"
            )

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
