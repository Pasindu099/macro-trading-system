"""Central bank monitor, policy and forecast data."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from typing import Any
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import CbPolicyDocument, CbEconomicProjection, Indicator, IndicatorRelease
def _now():
    from datetime import datetime

    return datetime.now(timezone.utc)


_MM_WATCHLIST = [
    {
        "id": "us", "bank": "FED", "name": "Federal Reserve",
        "country": "United States", "country_code": "US", "flag": "🇺🇸", "currency": "USD",
        "rate_indicator": "fed_interest_rate_decision", "inflation_target": 2.0,
        "metrics": [
            {"key": "core_pce", "canonical": "core_pce_price_index_yoy", "label": "Core PCE YoY", "unit": "%", "primary": True, "target": 2.0},
            {"key": "cpi",      "canonical": "cpi_headline_yoy",          "label": "Headline CPI YoY", "unit": "%", "target": 2.0},
            {"key": "core_cpi", "canonical": "core_cpi_yoy",              "label": "Core CPI YoY",     "unit": "%", "target": 2.0},
            {"key": "unemp",    "canonical": "unemployment_rate",          "label": "Unemployment",     "unit": "%", "target": 4.0, "lower_better": True},
            {"key": "nfp",      "canonical": "private_nonfarm_payrolls",  "label": "Private NFP",      "unit": "K"},
            {"key": "pmi",      "canonical": "ism_manufacturing_pmi",     "label": "ISM Mfg PMI",      "unit": "idx", "target": 50},
        ],
    },
    {
        "id": "eu", "bank": "ECB", "name": "European Central Bank",
        "country": "Eurozone", "country_code": "EU", "flag": "🇪🇺", "currency": "EUR",
        "rate_indicator": "ecb_deposit_rate", "inflation_target": 2.0,
        "metrics": [
            {"key": "cpi",      "canonical": "cpi_headline_yoy",  "label": "Headline CPI YoY", "unit": "%", "primary": True, "target": 2.0},
            {"key": "core_cpi", "canonical": "core_cpi_yoy",      "label": "Core CPI YoY",     "unit": "%", "target": 2.0},
            {"key": "gdp",      "canonical": "gdp_qoq",           "label": "GDP QoQ",          "unit": "%"},
            {"key": "unemp",    "canonical": "unemployment_rate", "label": "Unemployment",     "unit": "%", "lower_better": True},
            {"key": "mfg_pmi",  "canonical": "manufacturing_pmi", "label": "Mfg PMI",          "unit": "idx", "target": 50},
            {"key": "svc_pmi",  "canonical": "services_pmi",      "label": "Services PMI",     "unit": "idx", "target": 50},
        ],
    },
    {
        "id": "uk", "bank": "BOE", "name": "Bank of England",
        "country": "United Kingdom", "country_code": "UK", "flag": "🇬🇧", "currency": "GBP",
        "rate_indicator": "bank_rate", "inflation_target": 2.0,
        "metrics": [
            {"key": "cpi",      "canonical": "cpi_headline_yoy",  "label": "Headline CPI YoY", "unit": "%", "primary": True, "target": 2.0},
            {"key": "core_cpi", "canonical": "core_cpi_yoy",      "label": "Core CPI YoY",     "unit": "%", "target": 2.0},
            {"key": "svc_pmi",  "canonical": "services_pmi",      "label": "Services PMI",     "unit": "idx", "target": 50},
            {"key": "gdp",      "canonical": "gdp_qoq",           "label": "GDP QoQ",          "unit": "%"},
            {"key": "unemp",    "canonical": "unemployment_rate", "label": "Unemployment",     "unit": "%", "lower_better": True},
            {"key": "emp",      "canonical": "employment_change", "label": "Employment Chg",  "unit": "K"},
        ],
    },
    {
        "id": "jp", "bank": "BOJ", "name": "Bank of Japan",
        "country": "Japan", "country_code": "JP", "flag": "🇯🇵", "currency": "JPY",
        "rate_indicator": "boj_interest_rate_decision", "inflation_target": 2.0,
        "metrics": [
            {"key": "core_ex", "canonical": "cpi_ex_food_energy_yoy", "label": "CPI ex Food & Energy", "unit": "%", "primary": True, "target": 2.0},
            {"key": "core_cpi","canonical": "core_cpi_yoy",           "label": "Core CPI YoY",        "unit": "%", "target": 2.0},
            {"key": "cpi",     "canonical": "cpi_headline_yoy",       "label": "Headline CPI YoY",    "unit": "%"},
            {"key": "tokyo",   "canonical": "tokyo_core_cpi_yoy",     "label": "Tokyo Core CPI",      "unit": "%"},
            {"key": "gdp",     "canonical": "gdp_qoq",                "label": "GDP QoQ",             "unit": "%"},
            {"key": "unemp",   "canonical": "unemployment_rate",      "label": "Unemployment",        "unit": "%", "lower_better": True},
        ],
    },
    {
        "id": "au", "bank": "RBA", "name": "Reserve Bank of Australia",
        "country": "Australia", "country_code": "AU", "flag": "🇦🇺", "currency": "AUD",
        "rate_indicator": "cash_rate", "inflation_target": 2.5,
        "metrics": [
            {"key": "trimmed", "canonical": "rba_trimmed_mean_cpi_yoy", "label": "Trimmed Mean CPI", "unit": "%", "primary": True, "target": 2.5},
            {"key": "cpi",     "canonical": "cpi_headline_yoy",         "label": "Headline CPI YoY",  "unit": "%", "target": 2.5},
            {"key": "unemp",   "canonical": "unemployment_rate",        "label": "Unemployment",      "unit": "%", "lower_better": True},
            {"key": "emp",     "canonical": "employment_change",        "label": "Employment Chg",    "unit": "K"},
            {"key": "mfg_pmi", "canonical": "manufacturing_pmi",        "label": "Mfg PMI",           "unit": "idx", "target": 50},
            {"key": "svc_pmi", "canonical": "services_pmi",             "label": "Services PMI",      "unit": "idx", "target": 50},
        ],
    },
    {
        "id": "ca", "bank": "BOC", "name": "Bank of Canada",
        "country": "Canada", "country_code": "CA", "flag": "🇨🇦", "currency": "CAD",
        "rate_indicator": "overnight_rate", "inflation_target": 2.0,
        "metrics": [
            {"key": "trimmed", "canonical": "cpi_trimmed_mean_yoy", "label": "CPI Trimmed Mean", "unit": "%", "primary": True, "target": 2.0},
            {"key": "median",  "canonical": "cpi_median_yoy",       "label": "CPI Median",       "unit": "%", "target": 2.0},
            {"key": "cpi",     "canonical": "cpi_headline_yoy",     "label": "Headline CPI YoY", "unit": "%", "target": 2.0},
            {"key": "gdp",     "canonical": "gdp_mom",              "label": "GDP MoM",          "unit": "%"},
            {"key": "unemp",   "canonical": "unemployment_rate",    "label": "Unemployment",     "unit": "%", "lower_better": True},
            {"key": "emp",     "canonical": "employment_change",    "label": "Employment Chg",   "unit": "K"},
        ],
    },
    {
        "id": "ch", "bank": "SNB", "name": "Swiss National Bank",
        "country": "Switzerland", "country_code": "CH", "flag": "🇨🇭", "currency": "CHF",
        "rate_indicator": "policy_rate", "inflation_target": 1.0,
        "metrics": [
            {"key": "cpi",   "canonical": "cpi_headline_yoy",  "label": "Headline CPI YoY", "unit": "%", "primary": True, "target": 1.0},
            {"key": "gdp",   "canonical": "gdp_qoq",           "label": "GDP QoQ",          "unit": "%"},
            {"key": "unemp", "canonical": "unemployment_rate", "label": "Unemployment",     "unit": "%", "lower_better": True},
        ],
    },
    {
        "id": "nz", "bank": "RBNZ", "name": "Reserve Bank of New Zealand",
        "country": "New Zealand", "country_code": "NZ", "flag": "🇳🇿", "currency": "NZD",
        "rate_indicator": "official_cash_rate", "inflation_target": 2.0,
        "metrics": [
            {"key": "cpi_qoq", "canonical": "cpi_headline_qoq",     "label": "CPI QoQ",         "unit": "%", "primary": True, "target": 0.5},
            {"key": "cpi",     "canonical": "cpi_headline_yoy",     "label": "Headline CPI YoY","unit": "%", "target": 2.0},
            {"key": "unemp",   "canonical": "unemployment_rate",    "label": "Unemployment",    "unit": "%", "lower_better": True},
            {"key": "emp",     "canonical": "employment_change_qoq","label": "Employment Chg QoQ","unit": "%"},
        ],
    },
]




















BANK_INDICATOR_MAP: dict[str, dict[str, tuple[str, str] | None]] = {
    "FED":  {
        "inflation": ("cpi_headline_yoy", "US"),
        "gdp": ("gdp_qoq", "US"),
        "unemployment": ("unemployment_rate", "US"),
    },
    "ECB":  {
        "inflation": ("cpi_headline_yoy", "EU"),
        "gdp": ("gdp_qoq", "EU"),
        "unemployment": ("unemployment_rate", "EU"),
    },
    "BOE":  {
        "inflation": ("cpi_headline_yoy", "UK"),
        "gdp": ("gdp_qoq", "UK"),
        "unemployment": ("unemployment_rate", "UK"),
    },
    "BOJ":  {
        "inflation": ("cpi_headline_yoy", "JP"),
        "gdp": ("gdp_qoq", "JP"),
        "unemployment": ("unemployment_rate", "JP"),
    },
    "RBA":  {
        "inflation": ("cpi_headline_yoy", "AU"),
        "gdp": None,
        "unemployment": ("unemployment_rate", "AU"),
    },
    "BOC":  {
        "inflation": ("cpi_headline_yoy", "CA"),
        "gdp": ("gdp_mom", "CA"),
        "unemployment": ("unemployment_rate", "CA"),
    },
    "SNB":  {
        "inflation": ("cpi_headline_yoy", "CH"),
        "gdp": ("gdp_qoq", "CH"),
        "unemployment": ("unemployment_rate", "CH"),
    },
    "RBNZ": {
        "inflation": ("cpi_headline_qoq", "NZ"),
        "gdp": None,
        "unemployment": ("unemployment_rate", "NZ"),
    },
}




async def get_macro_monitor_data(session: AsyncSession) -> list[dict[str, Any]]:
    """Fetch all monitored indicators and releases in two queries."""
    from collections import defaultdict
    from sqlalchemy import tuple_

    wanted = {(cb["country_code"], name)
              for cb in _MM_WATCHLIST
              for name in [cb["rate_indicator"], *(metric["canonical"] for metric in cb["metrics"])]}
    if not wanted:
        return []
    id_rows = (await session.execute(
        select(Indicator.id, Indicator.country_code, Indicator.canonical_name)
        .where(tuple_(Indicator.country_code, Indicator.canonical_name).in_(wanted))
    )).all()
    id_map = {(row.country_code, row.canonical_name): row.id for row in id_rows}
    cutoff = datetime.combine(date.today() - timedelta(days=548), datetime.min.time())
    releases = (await session.execute(
        select(IndicatorRelease.indicator_id, IndicatorRelease.actual, IndicatorRelease.released_at)
        .where(IndicatorRelease.indicator_id.in_(list(id_map.values())),
               IndicatorRelease.actual.is_not(None), IndicatorRelease.released_at >= cutoff)
        .order_by(IndicatorRelease.indicator_id, IndicatorRelease.released_at.asc())
    )).all() if id_map else []
    by_indicator: dict[int, dict[date, float]] = defaultdict(dict)
    for row in releases:
        by_indicator[row.indicator_id][row.released_at.date()] = float(row.actual)
    result = []
    for cb in _MM_WATCHLIST:
        country_code = cb["country_code"]
        metrics_data = {}
        for metric in cb["metrics"]:
            indicator_id = id_map.get((country_code, metric["canonical"]))
            dated = sorted(by_indicator.get(indicator_id, {}).items())
            metrics_data[metric["key"]] = {
                "current": dated[-1][1] if dated else None,
                "previous": dated[-2][1] if len(dated) >= 2 else None,
                "history": [[day, value] for day, value in dated],
            }
        rate_id = id_map.get((country_code, cb["rate_indicator"]))
        rates = sorted(by_indicator.get(rate_id, {}).items())
        result.append({
            "bank": cb["bank"], "country_code": country_code, "currency": cb["currency"],
            "inflation_target": cb["inflation_target"],
            "rate": rates[-1][1] if rates else None,
            "metric_indicators": {m["key"]: m["canonical"] for m in cb["metrics"]},
            "metrics_data": metrics_data,
        })
    return result


async def get_cb_policy_data(session: AsyncSession) -> dict[str, Any]:
    """Return analyzed policy documents and tone history without page styling."""
    cutoff = date.today() - timedelta(days=13 * 31)
    rows = (await session.execute(
        select(CbPolicyDocument).where(
            CbPolicyDocument.doc_date >= cutoff,
            CbPolicyDocument.analyzed_at.is_not(None),
            CbPolicyDocument.doc_type.in_(["statement", "report", "upload"]),
        ).order_by(CbPolicyDocument.bank.asc(), CbPolicyDocument.doc_date.asc())
    )).scalars().all()
    by_bank: dict[str, list[CbPolicyDocument]] = {}
    for row in rows:
        by_bank.setdefault(row.bank, []).append(row)
    banks = []
    for bank, docs in by_bank.items():
        banks.append({
            "bank": bank,
            "latest_tone_score": float(docs[-1].tone_score) if docs[-1].tone_score is not None else None,
            "latest_date": docs[-1].doc_date,
            "report_count": len(docs),
            "tone_history": [(doc.doc_date, float(doc.tone_score)) for doc in docs if doc.tone_score is not None],
            "reports": [{
                "document_id": doc.id, "date": doc.doc_date, "doc_type": doc.doc_type,
                "tone_score": float(doc.tone_score) if doc.tone_score is not None else None,
                "tone_change": doc.tone_change_vs_prior,
                "inflation_outlook": doc.inflation_outlook,
                "growth_outlook": doc.growth_outlook,
                "labor_outlook": doc.labor_outlook,
            } for doc in reversed(docs)],
        })
    return {
        "banks": banks,
        "total_reports": len(rows),
        "last_updated": max((row.analyzed_at for row in rows if row.analyzed_at), default=None),
    }


async def get_projections_data(session: AsyncSession) -> dict[str, Any]:
    """Build latest annual paths and comparisons with batched actual lookups."""
    from sqlalchemy import tuple_
    from sqlalchemy.orm import aliased

    projections = list((await session.execute(
        select(CbEconomicProjection).order_by(
            CbEconomicProjection.bank.asc(), CbEconomicProjection.projection_date.desc(),
            CbEconomicProjection.horizon_year.asc(),
        )
    )).scalars().all())
    current_year = date.today().year
    paths: dict[str, Any] = {}
    latest_dates: dict[str, date] = {}
    best: dict[tuple[str, int], CbEconomicProjection] = {}
    for row in projections:
        latest_dates.setdefault(row.bank, row.projection_date)
        if not (row.horizon_label and len(row.horizon_label) == 4 and row.horizon_label.isdigit()) or row.horizon_year is None:
            continue
        if row.projection_date == latest_dates[row.bank]:
            entry = paths.setdefault(row.bank, {"as_of": row.projection_date, "path": {}})["path"].setdefault(
                row.horizon_year, {"year": row.horizon_year, "inflation": None, "gdp": None, "unemployment": None})
            for metric, attribute in (("inflation", "inflation_forecast"), ("gdp", "gdp_forecast"),
                                      ("unemployment", "unemployment_forecast")):
                value = getattr(row, attribute)
                if entry[metric] is None and value is not None:
                    entry[metric] = float(value)
        if row.horizon_year >= current_year:
            best.setdefault((row.bank, row.horizon_year), row)
    for payload in paths.values():
        payload["path"] = sorted(payload["path"].values(), key=lambda item: item["year"])

    comparisons = []
    requested: set[tuple[str, str]] = set()
    for (bank, _year), row in best.items():
        for metric, attribute in (("inflation", "inflation_forecast"), ("gdp", "gdp_forecast"),
                                  ("unemployment", "unemployment_forecast")):
            if getattr(row, attribute) is not None:
                mapping = BANK_INDICATOR_MAP.get(bank, {}).get(metric)
                if mapping:
                    requested.add((mapping[1], mapping[0]))
    actuals: dict[tuple[str, str], float] = {}
    if requested:
        indicator_rows = (await session.execute(
            select(Indicator).where(tuple_(Indicator.country_code, Indicator.canonical_name).in_(requested))
        )).scalars().all()
        indicator_by_id = {indicator.id: indicator for indicator in indicator_rows}
        if indicator_by_id:
            ranked = select(
                IndicatorRelease,
                func.row_number().over(
                    partition_by=IndicatorRelease.indicator_id,
                    order_by=(IndicatorRelease.period_start_date.desc().nullslast(),
                              desc(IndicatorRelease.released_at)),
                ).label("row_number"),
            ).where(
                IndicatorRelease.indicator_id.in_(list(indicator_by_id)),
                IndicatorRelease.actual.is_not(None),
            ).subquery()
            release = aliased(IndicatorRelease, ranked)
            release_rows = (await session.execute(
                select(release).where(ranked.c.row_number == 1)
            )).scalars().all()
            for item in release_rows:
                indicator = indicator_by_id[item.indicator_id]
                actuals[(indicator.country_code, indicator.canonical_name)] = float(item.actual)
    for (bank, year), row in sorted(best.items()):
        for metric, attribute in (("inflation", "inflation_forecast"), ("gdp", "gdp_forecast"),
                                  ("unemployment", "unemployment_forecast")):
            forecast = getattr(row, attribute)
            mapping = BANK_INDICATOR_MAP.get(bank, {}).get(metric)
            if forecast is None or not mapping:
                continue
            actual = actuals.get((mapping[1], mapping[0]))
            if actual is None:
                continue
            forecast = float(forecast)
            deviation = actual - forecast
            comparisons.append({
                "bank": bank, "metric": metric, "projection_date": row.projection_date,
                "horizon": year, "projected_value": round(forecast, 2),
                "actual_value": round(actual, 2), "deviation": round(deviation, 2),
                "deviation_pct": round(deviation / forecast * 100, 1) if forecast else None,
            })
    return {"latest_path_by_bank": paths, "projection_comparison": comparisons,
            "has_projections": bool(projections)}
