"""Source dates and visible freshness states shared by pages and jobs status."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CONFIG_PATH = Path("config/freshness.yaml")
QUERIES = {
    "fx_spot": "SELECT max(observation_date) FROM fx_spot_observations WHERE quality_status = 'valid' AND NOT is_outlier AND observation_date <= current_date",
    "government_yields": "SELECT max(market_observation_date) FROM government_yield_observations WHERE quality_status = 'valid' AND NOT is_outlier AND market_observation_date <= current_date",
    "fred_daily": "SELECT max(observation_date) FROM fred_observations WHERE series_id IN ('VIXCLS', 'SP500', 'DCOILBRENTEU') AND observation_date <= current_date",
    "calendar": "SELECT max(retrieved_at) FROM indicator_releases",
    "news": "SELECT max(published_at) FROM raw_news WHERE source = 'investinglive' AND published_at <= now()",
    "cot": "SELECT max(report_date) FROM cot_positions WHERE report_date <= current_date",
    "macro_state": "SELECT max(date) FROM processed.currency_stance WHERE date <= current_date",
    "dominance": "SELECT max(snapshot_date) FROM dominance_snapshots WHERE window_days = 20 AND snapshot_date <= current_date",
}
SITUATION_SOURCES = {
    "energy_shock": ("fred_daily", "calendar"),
    "fr_fiscal_stress": ("government_yields",),
    "labor_deterioration": ("calendar",),
    "risk_off": ("fred_daily",),
    "bear_steepening": ("government_yields",),
    "yields_up_currency_down": ("government_yields", "fx_spot"),
    "correlation_break": ("government_yields", "fx_spot"),
    "intervention_risk_jpy": ("fx_spot",),
}
OVERVIEW_PANEL_SOURCES = {
    "hero": ("dominance", "macro_state", "fred_daily", "cot", "news"),
    "situations": ("fx_spot", "government_yields", "fred_daily", "calendar"),
    "news": ("news",),
    "ranking": ("macro_state",),
    "central-banks": ("government_yields", "fred_daily", "calendar"),
    "pairs": ("macro_state",),
    "events": ("calendar",),
}
DESK_PANEL_SOURCES = {
    "verdict": ("macro_state", "government_yields", "cot"),
    "situations": ("fx_spot", "government_yields", "fred_daily", "calendar"),
    "price": ("fx_spot",),
    "economy": ("macro_state",),
    "direction": ("macro_state",),
    "keydata": ("calendar",),
    "country": ("calendar", "government_yields"),
    "fedview": ("fred_daily",),
    "fedpath": ("fred_daily",),
    "priced": ("government_yields",),
    "curve": ("government_yields",),
    "gap": ("macro_state", "government_yields"),
    "positioning": ("cot", "fx_spot"),
    "catalysts": ("calendar",),
    "news": ("news",),
    "scenarios": ("macro_state", "government_yields"),
}


def config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def business_days_since(as_of: date, today: date) -> int:
    return sum((as_of + timedelta(days=offset)).weekday() < 5
               for offset in range(1, max(0, (today - as_of).days) + 1))


def status_for(source: str, as_of: date | datetime | None, *,
               now: datetime | None = None, rules: dict | None = None) -> dict:
    now = now or datetime.now(UTC)
    rule = (rules or config())[source]
    unit, limit = rule["unit"], int(rule["max_age"])
    if as_of is None:
        return {"source": source, "label": rule["label"], "as_of": None,
                "stale": True, "age": None, "unit": unit, "max_age": limit,
                "reason": "No data available"}
    if isinstance(as_of, datetime):
        stamp = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
        age = max(0, (now - stamp).total_seconds() / 3600) if unit == "hours" else max(0, (now.date() - stamp.date()).days)
        shown = stamp.strftime("%d %b %Y %H:%M UTC")
        as_of_value = stamp.isoformat()
    else:
        age = max(0, (now.date() - as_of).days)
        shown = as_of.strftime("%d %b %Y")
        as_of_value = as_of.isoformat()
    if unit == "business_days":
        age = business_days_since(as_of.date() if isinstance(as_of, datetime) else as_of, now.date())
    stale = age > limit
    reason = f"{age:g} {unit.replace('_', ' ')} old; limit {limit} {unit.replace('_', ' ')}" if stale else None
    return {"source": source, "label": rule["label"], "as_of": as_of_value,
            "as_of_label": shown, "stale": stale, "age": age, "unit": unit,
            "max_age": limit, "reason": reason}


async def get_data_freshness(session: AsyncSession, *, now: datetime | None = None) -> dict[str, dict]:
    now = now or datetime.now(UTC)
    rules = config()
    result = {}
    for source, query in QUERIES.items():
        value = (await session.execute(text(query))).scalar_one()
        result[source] = status_for(source, value, now=now, rules=rules)
    return result


def panel_rows(statuses: dict[str, dict], sources: tuple[str, ...]) -> list[dict]:
    return [statuses[source] for source in sources]


def situation_is_fresh(situation_id: str, statuses: dict[str, dict]) -> bool:
    return all(not statuses[source]["stale"] for source in SITUATION_SOURCES[situation_id])
