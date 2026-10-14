"""Source dates and visible freshness states shared by pages and jobs status."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from dateutil.easter import easter
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
    "situations": "SELECT max(observation_date) FROM fx_spot_observations WHERE quality_status = 'valid' AND NOT is_outlier AND observation_date <= current_date",
    "verdicts": "SELECT max(date) FROM processed.currency_stance WHERE date <= current_date",
}
DERIVED_JOBS = {
    "macro_state": ("job:macro_state:chain",),
    "dominance": ("job:dominance",),
    "situations": ("job:situations_daily",),
    # Verdicts are calculated on request from these successful upstream jobs.
    "verdicts": ("job:macro_state:chain", "job:situations_daily", "job:rates_derived"),
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
    "situations": ("situations", "fx_spot", "government_yields", "fred_daily", "calendar"),
    "news": ("news",),
    "ranking": ("macro_state",),
    "central-banks": ("government_yields", "fred_daily", "calendar"),
    "pairs": ("macro_state",),
    "events": ("calendar",),
}
# Central Bank Terminal panels (Step 12 Part G); manual-import rows are added per panel kind.
CB_PANEL_SOURCES = {
    "summary": ("calendar", "government_yields"),
    "headlines": ("news",),
    "speakers": ("news",),
    "balance": ("news",),
    "upcoming": ("calendar",),
    "proj-tracking": ("fred_daily",),
}
CB_PANEL_MANUAL = {"statement": "statements", "speakers": "speeches", "balance": "speeches", "library": "speeches",
                   "proj-revisions": "projections", "proj-vintages": "projections"}
DESK_PANEL_SOURCES = {
    "verdict": ("verdicts", "macro_state", "government_yields", "cot"),
    "situations": ("situations", "fx_spot", "government_yields", "fred_daily", "calendar"),
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


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))


def _observed(day: date) -> date:
    return day - timedelta(days=1) if day.weekday() == 5 else day + timedelta(days=1) if day.weekday() == 6 else day


@lru_cache(maxsize=16)
def us_federal_holidays(year: int) -> frozenset[date]:
    days = {_observed(date(year, 1, 1)), _nth_weekday(year, 1, 0, 3),
            _nth_weekday(year, 2, 0, 3), _nth_weekday(year, 9, 0, 1),
            _nth_weekday(year, 10, 0, 2), _nth_weekday(year, 11, 3, 4)}
    may_end = date(year, 5, 31)
    days.add(may_end - timedelta(days=(may_end.weekday() - 0) % 7))
    days.update(_observed(date(year, month, day)) for month, day in
                ((6, 19), (7, 4), (11, 11), (12, 25)))
    # New Year's Day can be observed on the preceding 31 December.
    days.add(_observed(date(year + 1, 1, 1)))
    return frozenset(days)


@lru_cache(maxsize=128)
def market_holidays(year: int, market: str) -> frozenset[date]:
    if market == "US":
        return us_federal_holidays(year)
    if market == "FX":
        return frozenset({_observed(date(year, 1, 1)), _observed(date(year, 12, 25))})
    fixed = {
        "DE": ((1, 1), (5, 1), (10, 3), (12, 25), (12, 26)),
        "FR": ((1, 1), (5, 1), (5, 8), (7, 14), (11, 1), (11, 11), (12, 25)),
        "UK": ((1, 1), (12, 25), (12, 26)),
        "JP": ((1, 1), (2, 11), (2, 23), (4, 29), (5, 3), (5, 4), (5, 5), (8, 11), (11, 3), (11, 23)),
        "AU": ((1, 1), (1, 26), (4, 25), (12, 25), (12, 26)),
        "NZ": ((1, 1), (1, 2), (2, 6), (4, 25), (12, 25), (12, 26)),
        "CA": ((1, 1), (7, 1), (9, 30), (11, 11), (12, 25), (12, 26)),
        "CH": ((1, 1), (1, 2), (8, 1), (12, 25), (12, 26)),
    }
    days = {_observed(date(year, month, day)) for month, day in fixed.get(market, ())}
    if market in {"DE", "FR", "UK", "AU", "NZ", "CA", "CH"}:
        days.update((easter(year) - timedelta(days=2), easter(year) + timedelta(days=1)))
    if market == "CA":
        days.add(_nth_weekday(year, 10, 0, 2))
    if market == "NZ":
        days.add(_nth_weekday(year, 10, 0, 4))
    if market == "UK":
        days.add(_nth_weekday(year, 5, 0, 1))
        for month in (5, 8):
            last = date(year, month + 1, 1) - timedelta(days=1)
            days.add(last - timedelta(days=last.weekday()))
    if market == "JP":
        days.add(_nth_weekday(year, 10, 0, 2))
    return frozenset(days)


def business_days_since(as_of: date, today: date, market: str = "FX") -> int:
    return sum((day := as_of + timedelta(days=offset)).weekday() < 5 and
               day not in market_holidays(day.year, market)
               for offset in range(1, max(0, (today - as_of).days) + 1))


def status_for(source: str, as_of: date | datetime | None, *,
               now: datetime | None = None, rules: dict | None = None,
               updated_at: datetime | None = None, market: str | None = None) -> dict:
    now = now or datetime.now(UTC)
    rule = (rules or config())[source]
    unit, limit = rule["unit"], int(rule["max_age"])
    reference = updated_at if source in DERIVED_JOBS else as_of
    if reference is None:
        shown = as_of.strftime("%d %b %Y") if as_of else None
        return {"source": source, "label": rule["label"], "as_of": as_of.isoformat() if as_of else None,
                "as_of_label": shown, "updated_at": None, "updated_at_label": None,
                "stale": True, "age": None, "unit": unit, "max_age": limit,
                "reason": "No successful job run" if source in DERIVED_JOBS else "No data available"}
    age_value = reference
    if as_of is None:
        as_of = reference
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
        age = business_days_since(age_value.date() if isinstance(age_value, datetime) else age_value,
                                  now.date(), market or rule.get("market", "FX"))
        age = max(0, age - int(rule.get("publication_lag", 0)))
    elif source in DERIVED_JOBS:
        stamp = age_value if age_value.tzinfo else age_value.replace(tzinfo=UTC)
        age = max(0, (now - stamp).total_seconds() / 3600) if unit == "hours" else max(0, (now.date() - stamp.date()).days)
    stale = age > limit
    reason = f"{age:g} {unit.replace('_', ' ')} old; limit {limit} {unit.replace('_', ' ')}" if stale else None
    return {"source": source, "label": rule["label"], "as_of": as_of_value,
            "as_of_label": shown, "stale": stale, "age": age, "unit": unit,
            "max_age": limit, "reason": reason,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "updated_at_label": updated_at.strftime("%d %b %Y %H:%M UTC") if updated_at else None}


async def get_data_freshness(session: AsyncSession, *, now: datetime | None = None) -> dict[str, dict]:
    now = now or datetime.now(UTC)
    rules = config()
    result = {}
    for source, query in QUERIES.items():
        value = (await session.execute(text(query))).scalar_one()
        updated_at = None
        if source in DERIVED_JOBS:
            runs = (await session.execute(text("""
                SELECT run_type, max(finished_at) FROM ingestion_runs
                WHERE status = 'success' AND run_type = ANY(:names)
                GROUP BY run_type
            """), {"names": list(DERIVED_JOBS[source])})).all()
            successful = {name: stamp for name, stamp in runs}
            if all(successful.get(name) for name in DERIVED_JOBS[source]):
                updated_at = min(successful.values())
        result[source] = status_for(source, value, now=now, rules=rules, updated_at=updated_at)
    markets = (await session.execute(text("""
        SELECT country_code, max(market_observation_date)
        FROM government_yield_observations
        WHERE quality_status = 'valid' AND NOT is_outlier AND market_observation_date <= current_date
        GROUP BY country_code
    """))).all()
    if markets:
        per_market = {market: status_for("government_yields", day, now=now, rules=rules,
                                         market=market)
                      for market, day in markets}
        oldest = max(per_market.values(), key=lambda row: row["age"])
        result["government_yields"] = {**oldest, "markets": per_market,
                                        "reason": f"{oldest['reason']} ({next(m for m, row in per_market.items() if row is oldest)})"
                                        if oldest["stale"] else None}
    return result


def panel_rows(statuses: dict[str, dict], sources: tuple[str, ...]) -> list[dict]:
    return [statuses[source] for source in sources]


def situation_is_fresh(situation_id: str, statuses: dict[str, dict]) -> bool:
    return all(not statuses[source]["stale"] for source in SITUATION_SOURCES[situation_id])


async def manual_import_rows(session: AsyncSession, bank: str | None = None) -> list[dict]:
    """Freshness rows for manual-import CB sources (Step 12): never 'stale', labelled with the last import date."""
    from app.services.cb_manual_imports import manual_sources
    from app.services.cb_summary import bank_config
    stamps = {(r.bank, r.kind): r.at for r in (await session.execute(text(
        "SELECT bank, kind, max(imported_at) AS at FROM cb_manual_imports GROUP BY bank, kind"))).all()}
    rows = []
    for code in ([bank] if bank else list(bank_config())):
        for kind in manual_sources(code):
            at = stamps.get((code, kind))
            rows.append({"source": f"cb_manual:{code}:{kind}", "label": f"{code} {kind}", "manual": True,
                         "stale": False, "reason": None, "as_of": at.date().isoformat() if at else None,
                         "as_of_label": at.strftime("%d %b %Y") if at else None,
                         "text": f"manual import — last updated {at:%d %b %Y}" if at else "manual import — never imported"})
    return rows
