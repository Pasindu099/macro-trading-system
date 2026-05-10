"""Build CB preferred-indicator currency strength scores.

Uses only the indicators each central bank explicitly monitors,
bypassing the correlation filter (which loses too many indicators
on 5 years of data).

Run after build_feature_layer.py (requires processed.indicator_features).

Usage:
    python -m scripts.build_cb_preferred_score
    python -m scripts.build_cb_preferred_score --output-dir data/cb_preferred
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import dispose_engine
from app.processing.cb_preferred_score import (
    CB_WATCHLIST,
    CBPreferredConfig,
    build_cb_preferred_score,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build CB preferred-indicator currency strength scores."
    )
    parser.add_argument(
        "--output-dir",
        default="data/cb_preferred",
        help="Directory for CSV/JSON exports (default: data/cb_preferred).",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    print("CB Preferred-Indicator Strength Score")
    print(f"Watchlist: {sum(len(v) for v in CB_WATCHLIST.values())} indicators "
          f"across {len(CB_WATCHLIST)} currencies")
    for cc, inds in CB_WATCHLIST.items():
        infl  = [i.canonical_name for i in inds if i.theme == "Inflation"]
        labor = [i.canonical_name for i in inds if i.theme == "Labor"]
        growth = [i.canonical_name for i in inds if i.theme == "Growth"]
        print(f"  {cc}: {len(infl)} inflation  {len(labor)} labor  {len(growth)} growth")
    print()

    result = await build_cb_preferred_score(
        Path(args.output_dir), CBPreferredConfig()
    )

    if "error" in result:
        print(f"ERROR: {result['error']}")
        await dispose_engine()
        return

    s = result["summary"]
    print(f"\nOutput → {result['output_dir']}")
    print("Tables:")
    for t in result["tables"]:
        print(f"  {t}")

    print(f"\nRows:       scores={s['summary'].get('score_rows')}  "
          f"rankings={s['summary'].get('ranking_rows')}")
    print(f"Date range: {s['summary'].get('first_date')} → {s['summary'].get('last_date')}")
    print(f"Currencies: {s['summary'].get('currencies')}")

    print("\nIndicator coverage on latest date:")
    for row in s.get("indicator_coverage", []):
        print(f"  {row['country_code']:2s}  "
              f"inflation={row['infl_indicators']}  "
              f"labor={row['labor_indicators']}  "
              f"growth={row['growth_indicators']}")

    print("\nLatest rankings:")
    for row in s.get("latest_rankings", []):
        print(
            f"  #{row['rank_strongest']}  {row['currency']:3s}  "
            f"score={float(row['cb_strength_score']):+.3f}  "
            f"pressure={float(row['cb_pressure_normalized']):+.3f}  "
            f"{row['strength_label']:12s}  {row['trend_label']:10s}  "
            f"[{row['confidence']}]"
        )

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
