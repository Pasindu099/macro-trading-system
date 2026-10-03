"""Repair latest flags for mapped releases with no period identity."""

import argparse
import asyncio
from datetime import date, datetime, timezone

from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.services.event_innovation_jobs import JOB_NAME, score_new_releases
from app.services.job_lock import job_lock


TARGETS = {
    ("AU", "cash_rate"), ("AU", "private_sector_credit_mom"),
    ("AU", "retail_sales_mom"), ("AU", "td_mi_inflation_gauge_mom"),
    ("CA", "overnight_rate"), ("CA", "vehicle_sales_mom"),
    ("CA", "wholesale_sales_mom"), ("CH", "policy_rate"),
    ("EU", "ecb_deposit_rate"), ("EU", "ecb_marginal_lending_rate"),
    ("JP", "adjusted_trade_balance"), ("JP", "balance_of_trade"),
    ("JP", "boj_interest_rate_decision"), ("JP", "machine_tool_orders_yoy"),
    ("NZ", "global_dairy_trade_price_index"), ("NZ", "imports"),
    ("NZ", "milk_auctions"), ("NZ", "official_cash_rate"),
    ("UK", "bank_rate"), ("UK", "house_price_index_mom"),
    ("UK", "house_price_index_yoy"), ("US", "avg_hourly_earnings_level"),
    ("US", "fed_interest_rate_decision"),
}


_RANKED = text("""
    WITH targets AS (
        SELECT DISTINCT indicator_id FROM indicator_releases
        WHERE indicator_id IS NOT NULL AND actual IS NOT NULL
          AND period_start_date IS NULL AND period IS NULL
    ), ranked AS (
        SELECT r.id, r.indicator_id, i.country_code, i.canonical_name,
               r.released_at::date AS release_date, r.is_latest,
               ROW_NUMBER() OVER (
                   PARTITION BY r.indicator_id, r.released_at::date
                   ORDER BY r.retrieved_at DESC, r.id DESC
               ) = 1 AS should_be_latest
        FROM indicator_releases r
        JOIN indicators i ON i.id = r.indicator_id
        JOIN targets t ON t.indicator_id = r.indicator_id
        WHERE r.period_start_date IS NULL AND r.period IS NULL
    )
    SELECT * FROM ranked ORDER BY country_code, canonical_name, release_date, id
""")


async def _inspect(session):
    rows = [row for row in (await session.execute(_RANKED)).mappings()
            if (row["country_code"], row["canonical_name"]) in TARGETS]
    indicator_ids = {row["indicator_id"] for row in rows}
    if len(indicator_ids) != 23:
        raise RuntimeError(f"Expected 23 null-period indicators; found {len(indicator_ids)}")
    counts: dict[tuple[str, str], int] = {}
    first_dates: dict[int, date] = {}
    to_true, to_false = [], []
    for row in rows:
        key = (row["country_code"], row["canonical_name"])
        counts.setdefault(key, 0)
        first_dates[row["indicator_id"]] = min(
            first_dates.get(row["indicator_id"], row["release_date"]), row["release_date"]
        )
        if row["is_latest"] != row["should_be_latest"]:
            counts[key] += 1
            (to_true if row["should_be_latest"] else to_false).append(row["id"])
    return counts, first_dates, to_true, to_false


async def main(dry_run: bool) -> None:
    if dry_run:
        async with session_scope() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            counts, _, _, _ = await _inspect(session)
        print("dry_run", "indicators", len(counts), "rows_changed", sum(counts.values()))
    else:
        async with job_lock(JOB_NAME) as acquired:
            if not acquired:
                raise RuntimeError("Event Innovation job lock is held")
            async with run_logger("job:null_period_repair") as run:
                async def _transaction():
                    async with session_scope(statement_timeout="15min") as session:
                        counts, first_dates, to_true, to_false = await _inspect(session)
                        if to_true:
                            await session.execute(text("""
                                UPDATE indicator_releases SET is_latest = true WHERE id = ANY(:ids)
                            """), {"ids": to_true})
                        if to_false:
                            await session.execute(text("""
                                UPDATE indicator_releases SET is_latest = false WHERE id = ANY(:ids)
                            """), {"ids": to_false})
                        scores = await score_new_releases(
                            session, datetime.min.replace(tzinfo=timezone.utc), first_dates=first_dates,
                        )
                    return counts, scores

                counts, scores = await asyncio.wait_for(_transaction(), timeout=16 * 60)
                run.record_rows(scores)
        print("real", "indicators", len(counts), "rows_changed", sum(counts.values()),
              "event_innovation_rows", scores)
    for (country, name), changed in counts.items():
        print(country, name, changed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.dry_run))
