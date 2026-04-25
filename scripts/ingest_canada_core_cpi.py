"""Ingest Canada preferred core CPI measures from the Bank of Canada.

EODHD does not currently populate Canada's CPI-common, CPI-median, and
CPI-trim rows in our release table. The Bank of Canada publishes those
preferred core inflation measures directly through its Valet JSON feed.

Usage:
    python -m scripts.ingest_canada_core_cpi
    python -m scripts.ingest_canada_core_cpi --start-date 2024-01-01
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text

from app.db.session import session_scope
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)

BOC_CPI_URL = (
    "https://www.bankofcanada.ca/valet/observations/"
    "STATIC_TOTALCPICHANGE,CPI_TRIM,CPI_MEDIAN,CPI_COMMON,"
    "ATOM_V41693242,STATIC_CPIXFET,CPIW/json"
)

SERIES_TO_INDICATOR = {
    "CPI_COMMON": "cpi_common_yoy",
    "CPI_MEDIAN": "cpi_median_yoy",
    "CPI_TRIM": "cpi_trimmed_mean_yoy",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-date",
        default="2024-01-01",
        help="First observation date to ingest, YYYY-MM-DD (default: 2024-01-01)",
    )
    return parser.parse_args()


def fetch_boc_cpi(start_date: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"start_date": start_date})
    url = f"{BOC_CPI_URL}?{query}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def next_month_release_at(period_start: date) -> datetime:
    """Approximate the CPI publication timestamp from the observation month."""
    if period_start.month == 12:
        release_date = date(period_start.year + 1, 1, 20)
    else:
        release_date = date(period_start.year, period_start.month + 1, 20)
    return datetime.combine(release_date, time(12, 30), tzinfo=timezone.utc)


async def indicator_ids() -> dict[str, int]:
    async with session_scope() as session:
        result = await session.execute(
            text(
                """
                SELECT canonical_name, id
                FROM indicators
                WHERE country_code = 'CA'
                  AND canonical_name = ANY(:canonical_names)
                """
            ),
            {"canonical_names": list(SERIES_TO_INDICATOR.values())},
        )
        rows = dict(result.all())

    missing = sorted(set(SERIES_TO_INDICATOR.values()) - set(rows))
    if missing:
        raise RuntimeError(f"Missing Canada indicator definitions: {missing}")
    return rows


async def upsert_release(
    indicator_id: int,
    period_start: date,
    actual: Decimal,
    previous: Decimal | None,
    series_code: str,
    source_payload: dict[str, Any],
) -> bool:
    released_at = next_month_release_at(period_start)
    period = period_start.strftime("%b %Y")
    change = actual - previous if previous is not None else None
    change_percentage = (
        (change / abs(previous)) * Decimal("100")
        if previous not in (None, Decimal("0")) and change is not None
        else None
    )
    raw_payload = {
        "source": "Bank of Canada Valet",
        "source_url": BOC_CPI_URL,
        "series_code": series_code,
        "observation_date": period_start.isoformat(),
        "payload": source_payload,
    }

    async with session_scope() as session:
        update_result = await session.execute(
            text(
                """
                UPDATE indicator_releases
                SET period = :period,
                    released_at = :released_at,
                    actual = :actual,
                    previous = :previous,
                    estimate = NULL,
                    change = :change,
                    change_percentage = :change_percentage,
                    retrieved_at = now(),
                    is_latest = true,
                    raw_payload = CAST(:raw_payload AS jsonb)
                WHERE indicator_id = :indicator_id
                  AND period_start_date = :period_start_date
                """
            ),
            {
                "indicator_id": indicator_id,
                "period": period,
                "period_start_date": period_start,
                "released_at": released_at,
                "actual": actual,
                "previous": previous,
                "change": change,
                "change_percentage": change_percentage,
                "raw_payload": json.dumps(raw_payload),
            },
        )
        if update_result.rowcount:
            return False

        await session.execute(
            text(
                """
                INSERT INTO indicator_releases (
                    indicator_id,
                    period,
                    period_start_date,
                    released_at,
                    actual,
                    previous,
                    estimate,
                    change,
                    change_percentage,
                    is_latest,
                    raw_payload
                )
                VALUES (
                    :indicator_id,
                    :period,
                    :period_start_date,
                    :released_at,
                    :actual,
                    :previous,
                    NULL,
                    :change,
                    :change_percentage,
                    true,
                    CAST(:raw_payload AS jsonb)
                )
                """
            ),
            {
                "indicator_id": indicator_id,
                "period": period,
                "period_start_date": period_start,
                "released_at": released_at,
                "actual": actual,
                "previous": previous,
                "change": change,
                "change_percentage": change_percentage,
                "raw_payload": json.dumps(raw_payload),
            },
        )
        return True


async def main_async(args: argparse.Namespace) -> int:
    payload = fetch_boc_cpi(args.start_date)
    observations = payload.get("observations", [])
    ids = await indicator_ids()

    previous_by_series: dict[str, Decimal] = {}
    inserted = 0
    updated = 0

    for observation in observations:
        period_start = datetime.strptime(observation["d"], "%Y-%m-%d").date()
        for series_code, canonical_name in SERIES_TO_INDICATOR.items():
            value = parse_decimal(observation.get(series_code, {}).get("v"))
            if value is None:
                continue

            created = await upsert_release(
                indicator_id=ids[canonical_name],
                period_start=period_start,
                actual=value,
                previous=previous_by_series.get(series_code),
                series_code=series_code,
                source_payload=observation.get(series_code, {}),
            )
            if created:
                inserted += 1
            else:
                updated += 1
            previous_by_series[series_code] = value

    logger.info(
        "Canada core CPI ingest complete: inserted=%s updated=%s",
        inserted,
        updated,
    )
    return 0


def main() -> int:
    configure_logging()
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
