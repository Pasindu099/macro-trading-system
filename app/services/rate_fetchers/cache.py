"""Shared cache helpers for rate probability fetchers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_ois_curve(
    db_session: AsyncSession,
    *,
    bank: str,
    curve_date: date,
    values: dict[int, float],
    source: str,
) -> int:
    inserted = 0
    for tenor_days, rate in values.items():
        await db_session.execute(
            text(
                """
                INSERT INTO ois_cache (bank, curve_date, tenor_days, rate, source)
                VALUES (:bank, :curve_date, :tenor_days, :rate, :source)
                ON CONFLICT (bank, curve_date, tenor_days)
                DO UPDATE SET rate = EXCLUDED.rate,
                              source = EXCLUDED.source,
                              fetched_at = now()
                """
            ),
            {
                "bank": bank.upper(),
                "curve_date": curve_date,
                "tenor_days": int(tenor_days),
                "rate": Decimal(str(rate)),
                "source": source,
            },
        )
        inserted += 1
    return inserted


async def load_cached_curve(
    db_session: AsyncSession,
    *,
    bank: str,
    curve_date: date | None = None,
    source: str | None = None,
) -> dict[int, float]:
    filters = ["bank = :bank"]
    params: dict[str, object] = {"bank": bank.upper()}
    if curve_date is not None:
        filters.append("curve_date <= :curve_date")
        params["curve_date"] = curve_date
    if source is not None:
        filters.append("source = :source")
        params["source"] = source

    result = await db_session.execute(
        text(
            f"""
            SELECT tenor_days, rate
            FROM ois_cache
            WHERE {' AND '.join(filters)}
              AND curve_date = (
                SELECT MAX(curve_date)
                FROM ois_cache
                WHERE {' AND '.join(filters)}
              )
            ORDER BY tenor_days
            """
        ),
        params,
    )
    return {
        int(row.tenor_days): float(row.rate)
        for row in result.all()
        if row.tenor_days is not None and row.rate is not None
    }


async def has_cache_for_date(db_session: AsyncSession, target_date: date) -> bool:
    result = await db_session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1 FROM ois_cache WHERE curve_date = :target_date
            )
            """
        ),
        {"target_date": target_date},
    )
    return bool(result.scalar())


async def count_banks_with_cache_for_date(db_session: AsyncSession, target_date: date) -> int:
    result = await db_session.execute(
        text(
            """
            SELECT COUNT(DISTINCT bank)
            FROM ois_cache
            WHERE curve_date = :target_date
            """
        ),
        {"target_date": target_date},
    )
    return int(result.scalar() or 0)


async def count_banks_fetched_on_date(
    db_session: AsyncSession,
    target_date: date,
    banks: tuple[str, ...],
) -> int:
    result = await db_session.execute(
        text(
            """
            SELECT COUNT(DISTINCT bank)
            FROM ois_cache
            WHERE fetched_at::date = :target_date
              AND bank = ANY(:banks)
            """
        ),
        {"target_date": target_date, "banks": list(banks)},
    )
    return int(result.scalar() or 0)
