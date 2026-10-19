"""Episode membership and source-attributed daily market observations."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.eodhd_client import EODHDClient
from app.services.fred import ingest_fred_series

FRED_ASSETS = {
    "DCOILBRENTEU": "Brent", "DCOILWTICO": "WTI", "VIXCLS": "VIX",
    "DGS2": "US_2Y", "DGS10": "US_10Y", "DFII10": "US_REAL_10Y",
    "T10YIE": "US_10Y_BREAKEVEN", "SP500": "SP500",
}
FX_ASSETS = {"USD/DXY": "DXY", "USD/JPY": "USDJPY", "USD/CHF": "USDCHF", "EUR/USD": "EURUSD"}
METALS = {"XAUUSD.FOREX": "XAUUSD", "XAGUSD.FOREX": "XAGUSD"}


def valid_close(row: dict) -> Decimal | None:
    try:
        value = Decimal(str(row["close"]))
    except (KeyError, TypeError, InvalidOperation):
        return None
    return value if value.is_finite() and value > 0 else None


async def link_news_episodes() -> int:
    """Link tagged items only to matching episodes spanning their publication instant."""
    async with session_scope() as session:
        result = await session.execute(text("""
            INSERT INTO news_item_episodes (news_item_id, episode_id)
            SELECT n.id, e.id FROM news_items n JOIN situation_episodes e
              ON e.situation_id = ANY(n.situation_ids)
             AND (n.published_at AT TIME ZONE 'UTC')::date >= e.started_at
             AND (e.ended_at IS NULL OR (n.published_at AT TIME ZONE 'UTC')::date <= e.ended_at)
            ON CONFLICT DO NOTHING
        """))
        return result.rowcount


async def refresh_snapshot(*, from_date: date | None = None, fetch_metals: bool = True) -> dict[str, int]:
    """Refresh from durable FX/FRED history; fetch metals from EODHD EOD."""
    start = from_date or date(2000, 1, 1)
    async with session_scope() as session:
        await session.execute(text("""
            INSERT INTO cross_asset_daily_snapshots (observation_date, series_id, value, source, source_ref)
            SELECT observation_date, CASE pair
                WHEN 'USD/DXY' THEN 'DXY' WHEN 'USD/JPY' THEN 'USDJPY'
                WHEN 'USD/CHF' THEN 'USDCHF' WHEN 'EUR/USD' THEN 'EURUSD' END,
                close_value, provider, provider_symbol
            FROM (SELECT DISTINCT ON (pair, observation_date) pair, observation_date, close_value,
                         provider, provider_symbol FROM fx_spot_observations
                  WHERE pair IN ('USD/DXY','USD/JPY','USD/CHF','EUR/USD')
                    AND observation_date >= :start AND NOT is_outlier
                  ORDER BY pair, observation_date, ingested_at DESC, id DESC) fx
            ON CONFLICT (observation_date, series_id) DO UPDATE SET value = EXCLUDED.value,
                source = EXCLUDED.source, source_ref = EXCLUDED.source_ref, updated_at = now()
        """), {"start": start})
        await session.execute(text("""
            INSERT INTO cross_asset_daily_snapshots (observation_date, series_id, value, source, source_ref)
            SELECT observation_date, CASE series_id
                WHEN 'DCOILBRENTEU' THEN 'Brent' WHEN 'DCOILWTICO' THEN 'WTI'
                WHEN 'VIXCLS' THEN 'VIX' WHEN 'DGS2' THEN 'US_2Y'
                WHEN 'DGS10' THEN 'US_10Y' WHEN 'DFII10' THEN 'US_REAL_10Y'
                WHEN 'T10YIE' THEN 'US_10Y_BREAKEVEN' WHEN 'SP500' THEN 'SP500' END,
                value, 'fred', series_id
            FROM (SELECT DISTINCT ON (series_id, observation_date) series_id, observation_date, value
                  FROM fred_observations WHERE series_id = ANY(:series)
                    AND observation_date >= :start
                  ORDER BY series_id, observation_date, ingested_at DESC, id DESC) fred
            ON CONFLICT (observation_date, series_id) DO UPDATE SET value = EXCLUDED.value,
                source = EXCLUDED.source, source_ref = EXCLUDED.source_ref, updated_at = now()
        """), {"series": list(FRED_ASSETS), "start": start})
    if fetch_metals:
        async with EODHDClient() as client:
            for symbol, series in METALS.items():
                rows = await client.fetch_eod_history(symbol, from_date=start, to_date=date.today())
                values = []
                for row in rows:
                    close = valid_close(row)
                    if close is not None:
                        values.append({"day": date.fromisoformat(row["date"]), "series": series,
                                       "value": close, "symbol": symbol})
                if values:
                    async with session_scope() as session:
                        await session.execute(text("""
                            INSERT INTO cross_asset_daily_snapshots
                                (observation_date, series_id, value, source, source_ref)
                            VALUES (:day, :series, :value, 'eodhd', :symbol)
                            ON CONFLICT (observation_date, series_id) DO UPDATE SET value = EXCLUDED.value,
                                source = EXCLUDED.source, source_ref = EXCLUDED.source_ref, updated_at = now()
                        """), values)
    return await snapshot_coverage()


async def snapshot_coverage() -> dict[str, int]:
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT series_id, count(*) AS n FROM cross_asset_daily_snapshots GROUP BY series_id
        """))).mappings()
        return {row["series_id"]: row["n"] for row in rows}


async def run_daily_snapshot() -> dict[str, int]:
    return await refresh_snapshot(from_date=date.today() - timedelta(days=7))


async def backfill_snapshot() -> dict[str, int]:
    await ingest_fred_series(tuple(FRED_ASSETS), start=date(2000, 1, 1))
    return await refresh_snapshot()
