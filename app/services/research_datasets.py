"""Read-only research datasets, provenance, filters and bounded CSV export."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import text

from app.services.data_exports import csv_response
from app.services.fx_spot import FX_PAIR_SYMBOLS
from app.services.news_assets import FRED_ASSETS, FX_ASSETS, METALS
from app.settings import SUPPORTED_CURRENCIES

CONFIG_PATH = Path("config/research_datasets.yaml")
COLUMNS = ("date", "series", "currencies", "value", "source", "details")

# All SQL identifiers and expressions are fixed here; filters are bound parameters.
DATASETS = {
    "cross_asset": {
        "title": "Cross-asset snapshot", "frequency": "daily", "sources": ("eodhd", "fred"),
        "description": "Gold, silver, Brent, WTI, DXY, USD/JPY, USD/CHF, EUR/USD, VIX, US 2Y/10Y, real 10Y, breakeven and S&P 500.",
        "expected_series": sorted([*FRED_ASSETS.values(), *FX_ASSETS.values(), *METALS.values()]),
        "sql": """SELECT observation_date AS date, series_id AS series,
            CASE series_id WHEN 'USDJPY' THEN ARRAY['USD','JPY'] WHEN 'USDCHF' THEN ARRAY['USD','CHF']
                WHEN 'EURUSD' THEN ARRAY['EUR','USD'] ELSE ARRAY['USD'] END AS currencies,
            value, source, jsonb_build_object('source_ref',source_ref,'updated_at',updated_at) AS details
            FROM cross_asset_daily_snapshots""",
    },
    "fx_spot": {
        "title": "FX spot and computed DXY", "frequency": "daily", "sources": ("eodhd",),
        "description": "28 G10 pairs, USD/SEK and computed DXY. Latest valid, non-outlier observation per pair/day.",
        "expected_series": sorted([*FX_PAIR_SYMBOLS, "USD/DXY"]),
        "sql": """SELECT DISTINCT ON (pair,observation_date) observation_date AS date, pair AS series,
            ARRAY[base_currency,quote_currency]::text[] AS currencies, close_value AS value, provider AS source,
            jsonb_build_object('symbol',provider_symbol,'source_type',source_type,'ingested_at',ingested_at) AS details
            FROM fx_spot_observations WHERE quality_status='valid' AND NOT is_outlier
            ORDER BY pair,observation_date,ingested_at DESC,id DESC""",
    },
    "government_yields": {
        "title": "Government yields", "frequency": "daily", "sources": ("eodhd",),
        "description": "Latest valid, non-outlier sovereign yields per country, maturity and market date; values in percent.",
        "sql": """SELECT DISTINCT ON (country_code,maturity,market_observation_date)
            market_observation_date AS date, country_code||':'||maturity AS series,
            ARRAY[currency_code]::text[] AS currencies, yield_value AS value, provider AS source,
            jsonb_build_object('country',country_code,'maturity',maturity,'symbol',provider_symbol,
                'source_type',source_type,'ingested_at',ingested_at) AS details
            FROM government_yield_observations WHERE quality_status='valid' AND NOT is_outlier
            ORDER BY country_code,maturity,market_observation_date,ingested_at DESC,id DESC""",
    },
    "yield_spreads": {
        "title": "Yield spreads", "frequency": "daily", "sources": ("eodhd",),
        "description": "Aligned sovereign yield spreads, in basis points, with base and quote yields.",
        "sql": """SELECT obs_date AS date, spread_name||':'||tenor AS series,
            CASE WHEN spread_name='FR-DE' THEN ARRAY['EUR'] ELSE string_to_array(spread_name,'/') END AS currencies,
            spread_bp AS value, 'derived' AS source,
            jsonb_build_object('pair',spread_name,'tenor',tenor,'base_yield',base_yield,'quote_yield',quote_yield) AS details
            FROM yield_spreads""",
    },
    "cot": {
        "title": "COT positions", "frequency": "weekly (Friday publication)", "sources": ("cftc",),
        "description": "CFTC TFF positions by contract, currency and category. Net contracts plus long, short, spreading and open interest.",
        "sql": """SELECT report_date AS date, currency||':'||category||':'||contract_code AS series,
            ARRAY[currency]::text[] AS currencies, "long"-"short" AS value, 'cftc' AS source,
            jsonb_build_object('contract',contract_code,'category',category,'long',"long",'short',"short",
                'spreading',spreading,'open_interest',open_interest,'ingested_at',ingested_at) AS details
            FROM cot_positions""",
    },
    "retail": {
        "title": "Myfxbook retail snapshots", "frequency": "every 4 hours", "sources": ("myfxbook",),
        "description": "Official Myfxbook retail long/short percentages and position counts; every stored snapshot.",
        "sql": """SELECT taken_at::date AS date, pair AS series, string_to_array(pair,'/') AS currencies,
            long_pct AS value, source, jsonb_build_object('taken_at',taken_at,'short_pct',short_pct,
                'long_positions',long_positions,'short_positions',short_positions,'avg_long_price',avg_long_price,
                'avg_short_price',avg_short_price,'snapshot_id',id) AS details
            FROM retail_sentiment_snapshots WHERE source='myfxbook'""",
    },
    "model_rates": {
        "title": "Model-implied policy rates", "frequency": "daily", "sources": ("nyfed", "oecd", "calendar", "fred", "eodhd", "central_banks"),
        "description": "Experimental model rate with dated inputs, confidence, sensitivity range, market comparison and unavailable reasons.",
        "sql": """SELECT data_date AS date, bank AS series, ARRAY[CASE bank
            WHEN 'FED' THEN 'USD' WHEN 'ECB' THEN 'EUR' WHEN 'BOE' THEN 'GBP' WHEN 'BOJ' THEN 'JPY'
            WHEN 'BOC' THEN 'CAD' WHEN 'SNB' THEN 'CHF' WHEN 'RBA' THEN 'AUD' WHEN 'RBNZ' THEN 'NZD' END] AS currencies,
            model_rate AS value, 'derived' AS source, jsonb_build_object('inputs',inputs,'confidence',confidence,
                'status',status,'missing_inputs',missing_inputs,'model_version',model_version,'range_low',range_low,
                'range_high',range_high,'market_rate_3m',market_rate_3m,'gap_bp',gap_bp,'computed_at',computed_at) AS details
            FROM policy_model_daily""",
    },
    "situations": {
        "title": "Situation episodes", "frequency": "daily", "sources": ("eodhd", "fred", "calendar"),
        "description": "Deterministic episode start/end dates, scope and full evidence. Dates filter by episode start.",
        "sql": """SELECT started_at AS date, situation_id AS series,
            CASE WHEN scope_key='GLOBAL' THEN ARRAY['USD','EUR','GBP','JPY','CAD','CHF','AUD','NZD']
                WHEN scope='pair' THEN string_to_array(scope_key,'/')
                ELSE ARRAY[split_part(scope_key,':',1)] END AS currencies,
            NULL::numeric AS value, 'derived' AS source, jsonb_build_object('episode_id',id,'scope',scope,
                'scope_key',scope_key,'ended_at',ended_at,'evidence',evidence) AS details
            FROM situation_episodes""",
    },
    "dominance": {
        "title": "Dominance snapshots", "frequency": "daily", "sources": ("eodhd", "fred", "calendar"),
        "description": "Four-factor attribution shares by window, with sample pair count and complete snapshot shares.",
        "sql": """SELECT snapshot_date AS date, factor.key||':'||window_days||'D' AS series,
            ARRAY['USD','EUR','GBP','JPY','CAD','CHF','AUD','NZD'] AS currencies,
            factor.value::numeric AS value, 'derived' AS source,
            jsonb_build_object('window_days',window_days,'pair_count',pair_count,'shares',shares,'created_at',created_at) AS details
            FROM dominance_snapshots CROSS JOIN LATERAL jsonb_each_text(shares) factor""",
    },
}


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def dataset_spec(name: str, *, config: dict | None = None) -> dict:
    cfg = config if config is not None else load_config()
    if name not in DATASETS:
        raise LookupError("Dataset not found")
    spec = DATASETS[name]
    sources = [{"id": key, **cfg["sources"][key]} for key in spec["sources"]]
    licensed = all(source.get("licensed_for_redistribution", False) for source in sources)
    if not licensed and not cfg.get("show_unlicensed_sources", False):
        raise LookupError("Dataset excluded by redistribution policy")
    return {**spec, "name": name, "sources": sources, "licensed_for_redistribution": licensed,
            "licence_flag": "redistributable" if licensed else "personal use"}


def query(name: str, *, date_from: date | None = None, date_to: date | None = None,
          series: list[str] | None = None, currencies: list[str] | None = None) -> tuple[dict, str, dict]:
    spec = dataset_spec(name)
    if date_from and date_to and date_from > date_to:
        raise ValueError("Start date must not exceed end date")
    if series is not None and (len(series) > 100 or any(not isinstance(s, str) for s in series)):
        raise ValueError("Select at most 100 series")
    if currencies is not None and any(c not in (*SUPPORTED_CURRENCIES, "SEK") for c in currencies):
        raise ValueError("Unsupported dataset currency")
    sql = f"""SELECT * FROM ({spec['sql']}) dataset WHERE date <= current_date
        AND (CAST(:date_from AS date) IS NULL OR date >= :date_from)
        AND (CAST(:date_to AS date) IS NULL OR date <= :date_to)
        AND (CAST(:series AS text[]) IS NULL OR series=ANY(:series))
        AND (CAST(:currencies AS text[]) IS NULL OR currencies && :currencies)"""
    return spec, sql, {"date_from": date_from, "date_to": date_to, "series": series, "currencies": currencies}


async def describe(session, name: str, **filters) -> dict:
    spec, sql, params = query(name, **filters)
    rows = (await session.execute(text(f"""
        SELECT series, min(date) AS first_date, max(date) AS last_date, count(*) AS rows,
               array_agg(DISTINCT source) AS sources FROM ({sql}) selected GROUP BY series ORDER BY series
    """), params)).mappings().all()
    coverage = [dict(row) for row in rows]
    return {key: value for key, value in spec.items() if key != "sql"} | {
        "coverage": {"first_date": min((r["first_date"] for r in coverage), default=None),
                     "last_date": max((r["last_date"] for r in coverage), default=None),
                     "rows": sum(r["rows"] for r in coverage)},
        "series": coverage, "status": "available" if coverage else "empty"}


async def catalogue(session) -> list[dict]:
    output = []
    for name in DATASETS:
        try:
            dataset_spec(name)
        except LookupError:
            continue
        output.append(await describe(session, name))
    return output


async def browse(session, name: str, *, limit: int = 500, offset: int = 0, **filters) -> dict:
    if not 1 <= limit <= 50_000 or offset < 0:
        raise ValueError("Invalid dataset pagination")
    _, sql, params = query(name, **filters)
    meta = await describe(session, name, **filters)
    rows = (await session.execute(text(sql + " ORDER BY date DESC,series,details::text LIMIT :limit OFFSET :offset"),
                                  {**params, "limit": limit, "offset": offset})).mappings().all()
    return {"dataset": meta, "rows": [dict(row) for row in rows], "limit": limit, "offset": offset}


def export_dataset(name: str, **filters):
    _, sql, params = query(name, **filters)
    return csv_response(sql + " ORDER BY date DESC,series,details::text LIMIT :export_limit", params, COLUMNS,
                        f"dataset-{name}.csv")
