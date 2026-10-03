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


async def _build_macro_monitor_data(session: AsyncSession) -> list[dict[str, Any]]:
    """Query live indicator data for each CB's key metrics."""
    from datetime import date as _date, timedelta as _td
    cutoff = _date.today() - _td(days=548)  # ~18 months

    result = []
    for cb in _MM_WATCHLIST:
        cc = cb["country_code"]
        canonical_names = [m["canonical"] for m in cb["metrics"]] + [cb["rate_indicator"]]

        # Fetch indicator IDs for this country
        id_rows = (await session.execute(
            select(Indicator.id, Indicator.canonical_name)
            .where(Indicator.country_code == cc, Indicator.canonical_name.in_(canonical_names))
        )).all()
        id_map = {row.canonical_name: row.id for row in id_rows}

        # Fetch last 18 months of releases for all relevant indicators
        if not id_map:
            result.append({"bank": cb["bank"], "country_code": cc, "currency": cb["currency"],
                           "inflation_target": cb["inflation_target"], "rate": None,
                           "metrics_data": {}})
            continue

        releases = (await session.execute(
            select(
                IndicatorRelease.indicator_id,
                IndicatorRelease.actual,
                IndicatorRelease.released_at,
            )
            .where(
                IndicatorRelease.indicator_id.in_(list(id_map.values())),
                IndicatorRelease.actual.is_not(None),
                IndicatorRelease.released_at >= datetime.combine(cutoff, datetime.min.time()),
            )
            .order_by(IndicatorRelease.indicator_id, IndicatorRelease.released_at.asc())
        )).all()

        # Group by indicator_id → dedupe by date (keep latest actual per calendar date)
        from collections import defaultdict
        by_indicator: dict[int, dict[str, float]] = defaultdict(dict)
        for r in releases:
            date_key = r.released_at.date().isoformat()
            by_indicator[r.indicator_id][date_key] = float(r.actual)

        # Build per-metric data
        metrics_data: dict[str, Any] = {}
        for m in cb["metrics"]:
            canonical = m["canonical"]
            ind_id = id_map.get(canonical)
            if ind_id is None or ind_id not in by_indicator:
                metrics_data[m["key"]] = {"current": None, "previous": None, "history": []}
                continue
            dated = sorted(by_indicator[ind_id].items())  # [(date_str, value), ...]
            history = [[d, v] for d, v in dated]
            current = dated[-1][1] if dated else None
            previous = dated[-2][1] if len(dated) >= 2 else None
            metrics_data[m["key"]] = {"current": current, "previous": previous, "history": history}

        # Rate
        rate_id = id_map.get(cb["rate_indicator"])
        rate_val = None
        if rate_id and rate_id in by_indicator:
            rate_val = sorted(by_indicator[rate_id].items())[-1][1]

        result.append({
            "bank": cb["bank"], "country_code": cc, "currency": cb["currency"],
            "inflation_target": cb["inflation_target"],
            "rate": rate_val,
            "metric_indicators": {m["key"]: m["canonical"] for m in cb["metrics"]},
            "metrics_data": metrics_data,
        })

    return result


















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


async def _build_projections_context(session: AsyncSession) -> dict[str, Any]:
    """Build template context for the Economic Projections tab."""
    from datetime import date as _date

    current_year = _date.today().year

    # Fetch all projections (all years) so we can build full paths
    all_proj_q = await session.execute(
        select(CbEconomicProjection)
        .order_by(
            CbEconomicProjection.bank.asc(),
            CbEconomicProjection.projection_date.desc(),  # newest first
            CbEconomicProjection.horizon_year.asc(),
        )
    )
    all_proj_rows = list(all_proj_q.scalars().all())

    def _is_annual_label(label: str | None) -> bool:
        """True only for plain 4-digit year labels like '2025', '2026'."""
        return bool(label and len(label) == 4 and label.isdigit())

    # ── Latest forecast path per bank ────────────────────────────────────────
    # For each bank: take the most-recent projection date, merge annual-horizon
    # rows into one "path" object keyed by horizon_year.
    latest_path_by_bank: dict[str, Any] = {}
    seen_latest_date: dict[str, str] = {}

    for row in all_proj_rows:
        bank = row.bank
        date_str = row.projection_date

        # Determine the most-recent projection date for this bank
        if bank not in seen_latest_date:
            seen_latest_date[bank] = date_str
        latest_date = seen_latest_date[bank]
        if date_str != latest_date:
            continue  # only process rows for the most-recent date

        if not _is_annual_label(row.horizon_label):
            continue  # skip quarterly / longer_run rows

        yr = row.horizon_year
        if yr is None:
            continue

        if bank not in latest_path_by_bank:
            latest_path_by_bank[bank] = {"as_of": date_str, "path": {}}

        entry = latest_path_by_bank[bank]["path"].setdefault(yr, {
            "year": yr, "inflation": None, "gdp": None, "unemployment": None,
        })
        if entry["inflation"] is None and row.inflation_forecast is not None:
            entry["inflation"] = float(row.inflation_forecast)
        if entry["gdp"] is None and row.gdp_forecast is not None:
            entry["gdp"] = float(row.gdp_forecast)
        if entry["unemployment"] is None and row.unemployment_forecast is not None:
            entry["unemployment"] = float(row.unemployment_forecast)

    # Serialise path dict → sorted list
    for bank, payload in latest_path_by_bank.items():
        payload["path"] = sorted(payload["path"].values(), key=lambda x: x["year"])

    # ── Comparison table: latest annual projection vs actual ─────────────────
    indicator_cache: dict[tuple[str, str], float | None] = {}

    async def _get_actual(canonical_name: str, country_code: str) -> float | None:
        key = (canonical_name, country_code)
        if key in indicator_cache:
            return indicator_cache[key]
        ind_q = await session.execute(
            select(Indicator).where(
                Indicator.canonical_name == canonical_name,
                Indicator.country_code == country_code,
            )
        )
        ind = ind_q.scalar_one_or_none()
        if ind is None:
            indicator_cache[key] = None
            return None
        rel_q = await session.execute(
            select(IndicatorRelease)
            .where(
                IndicatorRelease.indicator_id == ind.id,
                IndicatorRelease.actual.is_not(None),
            )
            .order_by(
                IndicatorRelease.period_start_date.desc().nullslast(),
                desc(IndicatorRelease.released_at),
            )
            .limit(1)
        )
        rel = rel_q.scalar_one_or_none()
        value = float(rel.actual) if rel and rel.actual is not None else None
        indicator_cache[key] = value
        return value

    # Most-recent annual projection per (bank, horizon_year) for current & next year
    best_proj: dict[tuple[str, int], CbEconomicProjection] = {}
    for row in all_proj_rows:
        if not _is_annual_label(row.horizon_label):
            continue
        if row.horizon_year is None or row.horizon_year < current_year:
            continue
        key = (row.bank, row.horizon_year)
        if key not in best_proj:  # rows are newest-first, so first = most recent
            best_proj[key] = row

    projection_comparison: list[dict[str, Any]] = []
    for (bank_code, horizon_year), proj in sorted(best_proj.items()):
        bank_map = BANK_INDICATOR_MAP.get(bank_code, {})
        for metric_key, label in [("inflation", "Inflation"), ("gdp", "GDP"), ("unemployment", "Unemployment")]:
            forecast_val: float | None = None
            if metric_key == "inflation" and proj.inflation_forecast is not None:
                forecast_val = float(proj.inflation_forecast)
            elif metric_key == "gdp" and proj.gdp_forecast is not None:
                forecast_val = float(proj.gdp_forecast)
            elif metric_key == "unemployment" and proj.unemployment_forecast is not None:
                forecast_val = float(proj.unemployment_forecast)
            if forecast_val is None:
                continue
            indicator_mapping = bank_map.get(metric_key)
            if not indicator_mapping:
                continue
            canon, country = indicator_mapping
            actual = await _get_actual(canon, country)
            if actual is None:
                continue
            deviation = actual - forecast_val
            deviation_pct = (deviation / forecast_val * 100) if forecast_val else None
            projection_comparison.append({
                "bank": bank_code,
                "metric": metric_key,
                "projection_date": proj.projection_date,
                "horizon": horizon_year,
                "projected_value": round(forecast_val, 2),
                "actual_value": round(actual, 2),
                "deviation": round(deviation, 2),
                "deviation_pct": round(deviation_pct, 1) if deviation_pct is not None else None,
            })

    return {
        "latest_path_by_bank": latest_path_by_bank,
        "projection_comparison": projection_comparison,
        "has_projections": len(all_proj_rows) > 0,
    }


async def get_macro_monitor_data(session: AsyncSession) -> list[dict[str, Any]]:
    return await _build_macro_monitor_data(session)


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
    return await _build_projections_context(session)
