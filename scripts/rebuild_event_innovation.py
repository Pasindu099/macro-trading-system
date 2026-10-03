"""One-off full reconciliation without truncation.

    python -m scripts.rebuild_event_innovation --dry-run
    python -m scripts.rebuild_event_innovation
"""

from __future__ import annotations

import argparse
import asyncio

from app.db.session import dispose_engine, session_scope
from app.ingestion.run_logger import run_logger
from app.services.event_innovation_jobs import JOB_NAME
from app.services.event_innovation_rebuild import apply_full_rebuild, plan_full_rebuild
from app.services.job_lock import job_lock
from scripts.diff_event_innovation_keys import _KEY_DIFF_SQL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile Event Innovation without truncation")
    parser.add_argument("--dry-run", action="store_true", help="Print counts; write nothing")
    parser.add_argument("--key-diff", action="store_true", help="Also print the read-only dedup-key diff")
    return parser.parse_args()


async def _run(dry_run: bool, key_diff: bool) -> None:
    async with job_lock(JOB_NAME) as acquired:
        if not acquired:
            print("Event Innovation lock is held; try again after the active run finishes.")
            return

        if dry_run:
            async with session_scope(statement_timeout="15min") as session:
                if key_diff:
                    diff = (await session.execute(_KEY_DIFF_SQL)).mappings().one()
                    print("Key diff:", dict(diff))
                plan = await asyncio.wait_for(plan_full_rebuild(session), timeout=16 * 60)
                for name, value in plan.counts().items():
                    print(f"{name}: {value}")
            return

        async with run_logger("job:event_innovation_rebuild") as run:
            async def transaction() -> tuple[dict[str, int], int]:
                async with session_scope(statement_timeout="15min") as session:
                    plan = await plan_full_rebuild(session)
                    rows = await apply_full_rebuild(session, plan)
                    return plan.counts(), rows

            counts, rows = await asyncio.wait_for(transaction(), timeout=16 * 60)
            run.record_rows(rows)
            for name, value in counts.items():
                print(f"{name}: {value}")
            print(f"rows_written: {rows}")


async def main() -> None:
    args = parse_args()
    try:
        await _run(args.dry_run, args.key_diff)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
