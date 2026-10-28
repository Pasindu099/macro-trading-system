"""Mapped indicator catalogue and complete, persisted-score release history."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.data_exports import csv_response

COUNTRIES = ("US", "EU", "UK", "JP", "CA", "CH", "AU", "NZ", "DE", "FR", "CN")
THEMES = ("inflation", "labor", "growth", "sentiment", "housing", "trade", "policy", "other")
THEME_NAMES = {"monetary policy": "policy", "central bank": "policy", "labour": "labor"}


def country_code(value: str) -> str:
    value = value.upper()
    if value not in COUNTRIES:
        raise ValueError("Unsupported library country")
    return value


def theme_for(category: str) -> str:
    category = category.strip().lower()
    return THEME_NAMES.get(category, category if category in THEMES else "other")


async def catalogue(session: AsyncSession, country: str, *, theme: str | None = None,
                    importance: int | None = None, search: str = "") -> dict[str, Any]:
    country = country_code(country)
    if theme is not None and theme not in THEMES:
        raise ValueError("Unsupported theme")
    if importance is not None and importance not in (1, 2, 3):
        raise ValueError("Importance must be 1, 2 or 3")
    rows = (await session.execute(text("""
        SELECT i.id, i.canonical_name, i.display_name, i.primary_category, i.unit,
               i.frequency, i.importance, r.actual AS latest_value, r.released_at AS latest_date,
               COALESCE(r.previous, prior.actual) AS previous_value,
               s.surprise_normalized AS surprise_sigma, upcoming.next_release
        FROM indicators i
        LEFT JOIN LATERAL (
            SELECT * FROM indicator_releases WHERE indicator_id=i.id
            AND actual IS NOT NULL AND released_at <= now()
            ORDER BY period_start_date DESC NULLS LAST, released_at DESC, retrieved_at DESC, id DESC LIMIT 1
        ) r ON true
        LEFT JOIN event_innovation_scores s ON s.release_id=r.id
        LEFT JOIN LATERAL (
            SELECT actual FROM indicator_releases p WHERE p.indicator_id=i.id
            AND p.actual IS NOT NULL AND p.released_at <= now()
            AND COALESCE(p.period_start_date::text, p.period, p.released_at::date::text)
                <> COALESCE(r.period_start_date::text, r.period, r.released_at::date::text)
            ORDER BY p.period_start_date DESC NULLS LAST, p.released_at DESC, p.retrieved_at DESC, p.id DESC LIMIT 1
        ) prior ON true
        LEFT JOIN LATERAL (
            SELECT min(released_at) AS next_release FROM indicator_releases
            WHERE indicator_id=i.id AND released_at > now() AND actual IS NULL
        ) upcoming ON true
        WHERE i.country_code=:country
        ORDER BY i.importance, i.display_name
    """), {"country": country})).mappings().all()
    grouped = {key: [] for key in THEMES}
    for raw in rows:
        row = dict(raw)
        row["theme"] = theme_for(row.pop("primary_category"))
        if ((theme and row["theme"] != theme) or
            (importance is not None and row["importance"] != importance) or
            (search.casefold() not in f"{row['display_name']} {row['canonical_name']}".casefold())):
            continue
        grouped[row["theme"]].append(row)
    return {"country": country, "calendar_only": country == "CN", "themes": grouped,
            "count": sum(map(len, grouped.values()))}


# Each stored print keeps its own persisted surprise. Do not rescore revisions.
HISTORY_SQL = """
    WITH prints AS (
        SELECT r.*, COALESCE(r.period_start_date::text, r.period, r.released_at::date::text) AS period_key
        FROM indicator_releases r JOIN indicators i ON i.id=r.indicator_id
        WHERE i.country_code=:country AND i.canonical_name=:canonical
          AND r.released_at <= now() AND r.actual IS NOT NULL
    ), versions AS (
        SELECT p.*, first_value(actual) OVER chain AS first_actual,
               last_value(actual) OVER chain AS latest_actual,
               lag(actual) OVER (PARTITION BY indicator_id, period_key ORDER BY retrieved_at, id) AS prior_actual,
               row_number() OVER (PARTITION BY indicator_id, period_key ORDER BY retrieved_at, id) AS version
        FROM prints p WINDOW chain AS (PARTITION BY indicator_id, period_key
            ORDER BY retrieved_at, id ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
    ), counted AS (
        SELECT v.*, count(*) FILTER (WHERE v.version > 1 AND v.actual IS DISTINCT FROM v.prior_actual)
            OVER (PARTITION BY v.indicator_id, v.period_key) AS revision_count
        FROM versions v
    )
    SELECT v.id AS release_id, v.period, v.period_start_date, v.released_at, v.retrieved_at,
           v.actual, v.estimate AS consensus, v.previous, s.surprise_normalized AS surprise_sigma,
           v.first_actual, v.latest_actual, v.latest_actual-v.first_actual AS revision_size,
           v.actual-v.first_actual AS print_revision, v.version, v.revision_count
    FROM counted v LEFT JOIN event_innovation_scores s ON s.release_id=v.id
    WHERE (CAST(:date_from AS date) IS NULL OR v.released_at::date >= :date_from)
      AND (CAST(:date_to AS date) IS NULL OR v.released_at::date <= :date_to)
    ORDER BY v.released_at DESC, v.retrieved_at DESC, v.id DESC
"""
HISTORY_COLUMNS = ("release_id", "period", "period_start_date", "released_at", "retrieved_at", "actual",
                   "consensus", "previous", "surprise_sigma", "first_actual", "latest_actual",
                   "revision_size", "print_revision", "version", "revision_count")


async def indicator(session: AsyncSession, country: str, canonical: str) -> dict:
    country = country_code(country)
    row = (await session.execute(text("""
        SELECT id, country_code, canonical_name, display_name, frequency, importance, unit
        FROM indicators WHERE country_code=:country AND canonical_name=:canonical
    """), {"country": country, "canonical": canonical})).mappings().first()
    if row is None:
        raise LookupError("Indicator not found")
    return dict(row)


def history_params(country: str, canonical: str, date_from: date | None, date_to: date | None) -> dict:
    if date_from and date_to and date_from > date_to:
        raise ValueError("Start date must not exceed end date")
    return {"country": country_code(country), "canonical": canonical,
            "date_from": date_from, "date_to": date_to}


async def history(session: AsyncSession, country: str, canonical: str, *, limit: int = 500,
                  offset: int = 0, date_from: date | None = None, date_to: date | None = None) -> dict:
    if not 1 <= limit <= 50_000 or offset < 0:
        raise ValueError("Invalid history pagination")
    params = history_params(country, canonical, date_from, date_to)
    meta = await indicator(session, country, canonical)
    rows = (await session.execute(text(HISTORY_SQL + " LIMIT :limit OFFSET :offset"),
                                  {**params, "limit": limit, "offset": offset})).mappings().all()
    return {"indicator": meta, "rows": [dict(row) for row in rows], "limit": limit, "offset": offset}


async def export_history(session: AsyncSession, country: str, canonical: str, *,
                         date_from: date | None = None, date_to: date | None = None):
    params = history_params(country, canonical, date_from, date_to)
    meta = await indicator(session, country, canonical)
    return csv_response(HISTORY_SQL + " LIMIT :export_limit", params, HISTORY_COLUMNS,
                        f"indicator-{meta['id']}-history.csv")
