"""Backfill event_innovation_scores and release_bundles from release history.

Full rebuild (what you want the first time, after the 0016 migration):

    python -m scripts.build_event_innovation --truncate

Incremental re-score of one country, checking the numbers before writing:

    python -m scripts.build_event_innovation --country US --dry-run

Scales are point-in-time, so a *narrowed* run is not equivalent to a full one:
--date-from also truncates the history each release is normalized against.
Use it to re-score recent prints only when the scales are already stable.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date
from pathlib import Path

from app.db.session import dispose_engine, session_scope
from app.processing.event_innovation import build_event_innovation, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate the event innovation scoring layer."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to bundle_config.yaml. Defaults to config/bundle_config.yaml.",
    )
    parser.add_argument(
        "--country",
        default=None,
        help="Restrict to one country code (e.g. US). Default: all countries.",
    )
    parser.add_argument(
        "--date-from",
        type=date.fromisoformat,
        default=None,
        help="Only load releases on or after this date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Wipe both tables before writing. Use for a clean full rebuild.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and report, but write nothing.",
    )
    return parser.parse_args()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    if args.truncate and args.country:
        raise SystemExit(
            "--truncate wipes every country's rows; drop --country or drop --truncate."
        )

    config = load_config(args.config)
    print(
        f"Config: {len(config.bundles)} bundles, "
        f"{len(config.bucket_by_indicator)} bucketed indicators, "
        f"impact threshold <= {config.scored_importance_max}"
    )

    async with session_scope() as session:
        summary = await build_event_innovation(
            session,
            config=config,
            country_code=args.country.upper() if args.country else None,
            date_from=args.date_from,
            truncate=args.truncate,
            dry_run=args.dry_run,
        )

    print("Event innovation layer built." if not args.dry_run else "Dry run complete.")
    print(f"  releases loaded:      {summary['records_loaded']}")
    print(f"  rows produced:        {summary['scores_total']}")
    print(f"    scored:             {summary['scores_scored']}")
    print(f"    stored unscored:    {summary['scores_unscored']}")
    print(f"  bundles:              {summary['bundles']}")
    print(f"  awaiting CB roll-up:  {summary['meeting_adjacent_pending_rollup']}")
    if not args.dry_run:
        print(
            f"  written:              {summary['written']['bundles']} bundles, "
            f"{summary['written']['scores']} scores"
        )

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
