"""Source-backed situation evaluation, backtest, and daily persistence."""

from __future__ import annotations

import asyncio
import calendar
import json
from bisect import bisect_right
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker, session_scope
from app.ingestion.run_logger import run_logger
from app.services.fred import load_series
from app.services.job_lock import job_lock
from app.services.rates_drivers import calculate_drivers
from app.services.situation_rules import build_episodes, condition, current_state, load_config

COUNTRY_BENCHMARKS = {"USD": "US", "EUR": "DE", "GBP": "UK", "JPY": "JP"}
FRED_IDS = ("DCOILBRENTEU", "VIXCLS", "SP500")


class Series:
    def __init__(self, points: dict[date, float]):
        self.points = points
        self.days = sorted(points)

    def at(self, day: date, max_age: int | None = None) -> tuple[date, float] | None:
        index = bisect_right(self.days, day) - 1
        if index < 0 or max_age is not None and (day - self.days[index]).days > max_age:
            return None
        source_day = self.days[index]
        return source_day, self.points[source_day]

    def change(self, day: date, observations: int, *, percent: bool = False, max_age: int = 5) -> float | None:
        current = self.at(day, max_age)
        if current is None:
            return None
        index = bisect_right(self.days, current[0]) - 1
        if index < observations:
            return None
        previous = self.points[self.days[index - observations]]
        return ((current[1] / previous - 1) * 100 if previous else None) if percent else current[1] - previous

    def recent(self, day: date, count: int, max_age: int = 60) -> list[float] | None:
        current = self.at(day, max_age)
        if current is None:
            return None
        index = bisect_right(self.days, current[0])
        return [self.points[d] for d in self.days[index - count:index]] if index >= count else None


def months_before(day: date, months: int) -> date:
    month = day.year * 12 + day.month - 1 - months
    year, month_index = divmod(month, 12)
    return date(year, month_index + 1, min(day.day, calendar.monthrange(year, month_index + 1)[1]))


def _value(series: Series | None, day: date, max_age: int = 5) -> float | None:
    hit = series.at(day, max_age) if series else None
    return hit[1] if hit else None


def _pct(start: float | None, end: float | None) -> float | None:
    return (end / start - 1) * 100 if start not in (None, 0) and end is not None else None


async def load_history() -> dict[str, dict[Any, Series]]:
    history: dict[str, dict[Any, Series]] = {"fred": {}, "indicators": {}, "yields": {}, "fx": {}, "spreads": {}}
    for series_id in FRED_IDS:
        history["fred"][series_id] = Series(dict(await load_series(series_id, date(2019, 1, 1))))
    async with get_sessionmaker()() as session:
        indicator_rows = await session.execute(text("""
            SELECT DISTINCT ON (i.country_code, i.canonical_name, r.released_at::date)
                i.country_code, i.canonical_name, r.released_at::date AS day, r.actual::float AS value
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.canonical_name IN ('cpi_headline_yoy', 'core_cpi_yoy', 'unemployment_rate')
              AND r.is_latest AND r.actual IS NOT NULL
            ORDER BY i.country_code, i.canonical_name, r.released_at::date, r.retrieved_at DESC, r.id DESC
        """))
        yield_rows = await session.execute(text("""
            SELECT DISTINCT ON (country_code, maturity, market_observation_date)
                country_code, maturity, market_observation_date AS day, yield_value::float AS value
            FROM government_yield_observations
            WHERE maturity IN ('2Y', '10Y', '30Y') AND quality_status = 'valid' AND NOT is_outlier
            ORDER BY country_code, maturity, market_observation_date, ingested_at DESC, id DESC
        """))
        fx_rows = await session.execute(text("""
            SELECT DISTINCT ON (pair, observation_date) pair, observation_date AS day,
                close_value::float AS value
            FROM fx_spot_observations WHERE quality_status = 'valid' AND NOT is_outlier
            ORDER BY pair, observation_date, ingested_at DESC, id DESC
        """))
        spread_rows = await session.execute(text("""
            SELECT spread_name, tenor, obs_date AS day, spread_bp::float AS value
            FROM yield_spreads WHERE tenor IN ('2Y', '10Y')
        """))
        for row in indicator_rows:
            history["indicators"].setdefault((row.country_code, row.canonical_name), {})[row.day] = row.value
        for row in yield_rows:
            history["yields"].setdefault((row.country_code, row.maturity), {})[row.day] = row.value
        for row in fx_rows:
            history["fx"].setdefault(row.pair, {})[row.day] = row.value
        for row in spread_rows:
            history["spreads"].setdefault((row.spread_name, row.tenor), {})[row.day] = row.value
    for kind in ("indicators", "yields", "fx", "spreads"):
        history[kind] = {key: Series(points) for key, points in history[kind].items()}
    history["pair_common"] = {
        pair: sorted(set(history["spreads"][(pair, "2Y")].days) & set(spot.days))
        for pair, spot in history["fx"].items() if (pair, "2Y") in history["spreads"]
    }
    return history


def _currency_basket_change(currency: str, day: date, fx: dict[str, Series]) -> float | None:
    returns = []
    for pair, series in fx.items():
        if "/" not in pair or pair == "USD/DXY":
            continue
        base, quote = pair.split("/", 1)
        if currency not in (base, quote):
            continue
        move = series.change(day, 10, percent=True)
        if move is not None and move > -100:
            returns.append(move if base == currency else (1 / (1 + move / 100) - 1) * 100)
    return sum(returns) / len(returns) if len(returns) >= 3 else None


def features(situation_id: str, scope_key: str, day: date, history: dict[str, dict[Any, Series]],
             intervention_zone: tuple[float, float] = (158.0, 160.5)) -> dict[str, float | None]:
    fred, indicators = history["fred"], history["indicators"]
    yields, fx, spreads = history["yields"], history["fx"], history["spreads"]
    if situation_id == "energy_shock":
        brent = fred.get("DCOILBRENTEU")
        current = _value(brent, day, 7)
        previous = _value(brent, months_before(day, 3), 7)
        country = {"USD": "US", "EUR": "EU", "CAD": "CA"}[scope_key]
        headline = _value(indicators.get((country, "cpi_headline_yoy")), day, 60)
        core = _value(indicators.get((country, "core_cpi_yoy")), day, 60)
        return {"brent_3m_pct": _pct(previous, current),
                "headline_core_gap_pp": headline - core if headline is not None and core is not None else None}
    if situation_id == "fr_fiscal_stress":
        series = spreads.get(("FR-DE", "10Y"))
        return {"oat_bund_10y_bp": _value(series, day),
                "oat_bund_20d_change_bp": series.change(day, 20) if series else None}
    if situation_id == "labor_deterioration":
        country = scope_key.split(":", 1)[-1]
        series = indicators.get((country, "unemployment_rate"))
        three = series.recent(day, 3) if series else None
        twelve = series.recent(day, 12) if series else None
        return {"unemployment_gap_pp": sum(three) / 3 - min(twelve) if three and twelve else None}
    if situation_id == "risk_off":
        vix, sp500 = fred.get("VIXCLS"), fred.get("SP500")
        return {"vix": _value(vix, day, 7),
                "vix_5d_change_pts": vix.change(day, 5, max_age=7) if vix else None,
                "sp500_10d_pct": sp500.change(day, 10, percent=True, max_age=7) if sp500 else None}
    if situation_id == "bear_steepening":
        country = scope_key.split(":", 1)[-1]
        two, ten, thirty = (yields.get((country, tenor)) for tenor in ("2Y", "10Y", "30Y"))
        d2 = two.change(day, 20) * 100 if two and two.change(day, 20) is not None else None
        d10 = ten.change(day, 20) * 100 if ten and ten.change(day, 20) is not None else None
        d30 = thirty.change(day, 20) * 100 if thirty and thirty.change(day, 20) is not None else None
        curve = d30 - d10 if d30 is not None and d10 is not None else None
        return {"ten_s_thirty_s_1m_change_bp": curve,
                "two_y_change_less_than_curve": float(d2 < curve) if d2 is not None and curve is not None else None}
    if situation_id == "yields_up_currency_down":
        country = COUNTRY_BENCHMARKS[scope_key]
        two = yields.get((country, "2Y"))
        move = two.change(day, 10) * 100 if two and two.change(day, 10) is not None else None
        if scope_key == "USD":
            index = fx.get("USD/DXY")
            currency_move = index.change(day, 10, percent=True) if index else None
        elif scope_key == "EUR":
            index = fx.get("EUR/EER")
            currency_move = index.change(day, 10, percent=True) if index else None
        else:
            currency_move = _currency_basket_change(scope_key, day, fx)
        return {"two_y_10d_change_bp": move, "currency_index_10d_pct": currency_move}
    if situation_id == "correlation_break":
        spread, spot = spreads.get((scope_key, "2Y")), fx.get(scope_key)
        if not spread or not spot:
            return {"rates_drivers_diverging": None}
        common = history.get("pair_common", {}).get(scope_key)
        if common is None:
            common = sorted(set(spread.days) & set(spot.days))
        end = bisect_right(common, day)
        if end < 61 or (day - common[end - 1]).days > 5:
            return {"rates_drivers_diverging": None}
        dates = common[end - 61:end]
        result = calculate_drivers(scope_key, "2Y", {d: spread.points[d] for d in dates},
                                   {d: spot.points[d] for d in dates})
        return {"rates_drivers_diverging": float(result["status"] == "diverging")
                if result["status"] != "unavailable" else None}
    if situation_id == "intervention_risk_jpy":
        spot = _value(fx.get("USD/JPY"), day)
        low, high = intervention_zone
        distance = (low - spot) / low * 100 if spot is not None and spot < low else (
            (spot - high) / high * 100 if spot is not None and spot > high else 0 if spot is not None else None)
        return {"intervention_zone_distance_pct": distance, "usd_jpy": spot}
    raise ValueError(f"Unknown situation {situation_id}")


def scopes(spec: dict[str, Any], available_pairs: list[str]) -> list[str]:
    if "countries" in spec:
        return [f"{currency}:{country}" for country, currency in spec["countries"].items()]
    if spec.get("pairs") == "all":
        return available_pairs
    return spec.get("pairs", spec.get("currencies", []))


async def backtest_all() -> list[dict[str, Any]]:
    config = load_config()
    history = await load_history()
    available_pairs = sorted(pair for pair in history["fx"] if (pair, "2Y") in history["spreads"])
    output = []
    for situation_id, spec in config["situations"].items():
        for scope_key in scopes(spec, available_pairs):
            daily = []
            day = date(2020, 1, 1)
            while day <= date.today():
                daily.append((day, features(situation_id, scope_key, day, history,
                                            tuple(config["intervention_zone_jpy"]))))
                day += timedelta(days=1)
            episodes = build_episodes(spec, daily)
            latest_evidence = daily[-1][1]
            available = any(condition(spec["trigger"], row) is not None for _, row in daily)
            output.append({"situation_id": situation_id, "scope": spec["scope"], "scope_key": scope_key,
                           "name": spec["name"], "severity": spec["severity"],
                           "evidence_panel_id": spec["evidence_panel_id"],
                           "data_available": available,
                           "status": current_state(spec, episodes, latest_evidence) if available else "unavailable",
                           "episodes": episodes, "latest_evidence": latest_evidence})
    return output


async def get_situation_episodes(*, active: bool | None = None) -> list[dict[str, Any]]:
    """Stored episodes for desk panels and JSON clients."""
    query = """SELECT id, situation_id, scope, scope_key, started_at, ended_at, evidence
               FROM situation_episodes"""
    if active is True:
        query += " WHERE ended_at IS NULL"
    elif active is False:
        query += " WHERE ended_at IS NOT NULL"
    query += " ORDER BY started_at DESC, id DESC"
    async with get_sessionmaker()() as session:
        rows = await session.execute(text(query))
        items = [dict(row._mapping) for row in rows]
    specs = load_config()["situations"]
    return [{**row, "name": specs[row["situation_id"]]["name"],
             "severity": specs[row["situation_id"]]["severity"],
             "evidence_panel_id": specs[row["situation_id"]]["evidence_panel_id"]}
            for row in items]


async def run_situations_job() -> int:
    async with job_lock("situations") as acquired:
        async with run_logger("job:situations_daily") as run:
            if not acquired:
                run.mark_skipped()
                return 0
            results = await asyncio.wait_for(backtest_all(), timeout=16 * 60)
            rows = []
            for result in results:
                if not result["data_available"]:
                    continue
                for episode in result["episodes"]:
                    rows.append({"situation_id": result["situation_id"], "scope": result["scope"],
                                 "scope_key": result["scope_key"], "started_at": episode["started_at"],
                                 "ended_at": episode["ended_at"], "evidence": json.dumps(episode["evidence"])})
            async with session_scope(statement_timeout="15min") as session:
                for result in results:
                    if not result["data_available"]:
                        continue
                    await session.execute(text("""
                        DELETE FROM situation_episodes WHERE situation_id = :s AND scope_key = :k
                    """), {"s": result["situation_id"], "k": result["scope_key"]})
                if rows:
                    await session.execute(text("""
                        INSERT INTO situation_episodes
                            (situation_id, scope, scope_key, started_at, ended_at, evidence)
                        VALUES (:situation_id, :scope, :scope_key, :started_at, :ended_at, CAST(:evidence AS jsonb))
                    """), rows)
            run.record_rows(len(rows))
            return len(rows)
