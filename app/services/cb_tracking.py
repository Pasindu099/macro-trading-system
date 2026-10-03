"""Fed CB Tracking: data so far vs the latest SEP, revisions and the reaction-function flag.

Projections are Q4-over-Q4 (inflation, GDP) or Q4 averages (unemployment rate).
"Required pace" is the constant rate for the rest of the year that lands exactly on
the projection given the data already published.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services.fred import load_series

BANK = "FED"
INFLATION_BAND, UNEMPLOYMENT_BAND, GDP_BAND = 0.5, 0.2, 1.0
SERIES = {"pce_inflation": "PCEPI", "core_pce_inflation": "PCEPILFE", "unemployment_rate": "UNRATE", "real_gdp": "GDPC1"}
# Table 2 has one inflation error row ("total consumer prices"); core PCE uses it too.
ERROR_VARIABLE = {"core_pce_inflation": "pce_inflation"}


# ── Pure maths ─────────────────────────────────────────────────────────

def _bisect(f, lo: float, hi: float, tol: float = 1e-12) -> float:
    flo = f(lo)
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def required_monthly_pace(monthly: dict[int, float], base_q4_avg: float, projection_pct: float) -> float | None:
    """Annualised % pace for the remaining months so the Q4 average is projection% above last year's Q4 average.

    `monthly` maps month number (1–12) → index level for the projection year.
    """
    if not monthly:
        return None
    last_month = max(monthly)
    if last_month >= 12:
        return None  # year complete
    target_sum = 3 * base_q4_avg * (1 + projection_pct / 100)
    level = monthly[last_month]

    def q4_gap(g: float) -> float:
        total = sum(monthly[k] if k <= last_month else level * (1 + g) ** (k - last_month) for k in (10, 11, 12))
        return total - target_sum

    g = _bisect(q4_gap, -0.2, 0.2)
    return ((1 + g) ** 12 - 1) * 100


def annualised_3m(levels: list[float]) -> float | None:
    """Latest 3-month change, annualised (needs the level 3 months earlier)."""
    if len(levels) < 4:
        return None
    return ((levels[-1] / levels[-4]) ** 4 - 1) * 100


def required_quarterly_pace(quarters: dict[int, float], base_q4: float, projection_pct: float) -> float | None:
    """Annualised % growth needed in the remaining quarters to reach Q4/Q4 projection."""
    if not quarters:
        return None
    last_q = max(quarters)
    if last_q >= 4:
        return None
    remaining = 4 - last_q
    target = base_q4 * (1 + projection_pct / 100)
    quarterly = (target / quarters[last_q]) ** (1 / remaining) - 1
    return ((1 + quarterly) ** 4 - 1) * 100


def status_vs_required(actual: float | None, required: float | None, band: float) -> str | None:
    if actual is None or required is None:
        return None
    gap = actual - required
    return "running_hot" if gap > band else "running_cold" if gap < -band else "on_track"


def unemployment_status(latest_3m_avg: float | None, q4_projection: float, band: float = UNEMPLOYMENT_BAND) -> str | None:
    """Unemployment below the projection = a hotter labour market than the Fed expects."""
    if latest_3m_avg is None:
        return None
    gap = latest_3m_avg - q4_projection
    return "running_hot" if gap < -band else "running_cold" if gap > band else "on_track"


def reaction_flag(inflation_revision: float | None, funds_revision: float | None) -> str:
    """tolerance: inflation 2026 median up ≥ 0.2pp with the funds-rate median unchanged; response: both up."""
    if inflation_revision is None or funds_revision is None or inflation_revision < 0.2:
        return "none"
    if funds_revision == 0:
        return "tolerance"
    return "response" if funds_revision > 0 else "none"


# ── Data access ────────────────────────────────────────────────────────

async def sep_rounds() -> list[date]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text(
            "SELECT DISTINCT release_date FROM cb_projection_values WHERE bank = :b ORDER BY release_date"), {"b": BANK})
        return [r.release_date for r in rows]


async def round_values(release_date: date) -> dict[tuple[str, str, str], float]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT variable, horizon, stat, value::float AS value FROM cb_projection_values
            WHERE bank = :b AND release_date = :d
        """), {"b": BANK, "d": release_date})
        return {(r.variable, r.horizon, r.stat): r.value for r in rows}


async def projection_errors(year: int) -> dict[tuple[str, str], float]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT variable, horizon, rmse::float AS rmse FROM cb_projection_errors
            WHERE bank = :b AND publication_year = (
                SELECT max(publication_year) FROM cb_projection_errors WHERE bank = :b AND publication_year <= :y)
        """), {"b": BANK, "y": year})
        return {(r.variable, r.horizon): r.rmse for r in rows}


# ── Assembly ───────────────────────────────────────────────────────────

def _monthly_for_year(points: list[tuple[date, float]], year: int) -> dict[int, float]:
    return {d.month: v for d, v in points if d.year == year}


def _q4_avg(points: list[tuple[date, float]], year: int) -> float | None:
    q4 = [v for d, v in points if d.year == year and d.month >= 10]
    return sum(q4) / 3 if len(q4) == 3 else None


async def get_tracking(year: int | None = None) -> dict[str, Any]:
    rounds = await sep_rounds()
    if not rounds:
        return {"status": "unavailable", "reason": "No SEP rounds stored"}
    latest = rounds[-1]
    year = year or latest.year
    values = await round_values(latest)
    errors = await projection_errors(latest.year)
    out: dict[str, Any] = {"bank": BANK, "round": latest, "year": year, "variables": {}}
    for variable, series_id in SERIES.items():
        median = values.get((variable, str(year), "median"))
        if median is None:
            out["variables"][variable] = {"status": "unavailable", "reason": f"No {year} projection in the {latest} SEP"}
            continue
        rmse = errors.get((ERROR_VARIABLE.get(variable, variable), str(year)))
        entry: dict[str, Any] = {"projection": median, "series": series_id,
                                 "band_70": [round(median - rmse, 2), round(median + rmse, 2)] if rmse else None,
                                 "band_source": "SEP Table 2 RMSE" + (" (total consumer prices)" if variable in ERROR_VARIABLE else "")}
        points = await load_series(series_id, date(year - 2, 1, 1))
        if not points:
            entry.update(status="unavailable", reason=f"No {series_id} data stored")
        elif variable in ("pce_inflation", "core_pce_inflation"):
            base = _q4_avg(points, year - 1)
            monthly = _monthly_for_year(points, year)
            required = required_monthly_pace(monthly, base, median) if base else None
            actual = annualised_3m([v for _, v in points])
            entry.update(latest_month=points[-1][0], required_pace=_r(required), actual_3m_ann=_r(actual),
                         status=status_vs_required(actual, required, INFLATION_BAND) or "unavailable")
        elif variable == "unemployment_rate":
            latest_avg = sum(v for _, v in points[-3:]) / 3 if len(points) >= 3 else None
            entry.update(latest_month=points[-1][0], latest_3m_avg=_r(latest_avg),
                         status=unemployment_status(latest_avg, median) or "unavailable")
        else:  # real GDP, quarterly
            base = next((v for d, v in points if d.year == year - 1 and d.month == 10), None)
            quarters = {(d.month - 1) // 3 + 1: v for d, v in points if d.year == year}
            required = required_quarterly_pace(quarters, base, median) if base else None
            actual = ((points[-1][1] / points[-2][1]) ** 4 - 1) * 100 if len(points) >= 2 else None
            entry.update(latest_quarter=points[-1][0], required_pace=_r(required), actual_q_ann=_r(actual),
                         status=status_vs_required(actual, required, GDP_BAND) or "unavailable")
        out["variables"][variable] = entry
    return out


def _r(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


async def get_revisions() -> dict[str, Any]:
    rounds = await sep_rounds()
    if len(rounds) < 2:
        return {"status": "unavailable", "reason": "Fewer than two SEP rounds stored"}
    latest, previous = rounds[-1], rounds[-2]
    now, before = await round_values(latest), await round_values(previous)
    rows = []
    for (variable, horizon, stat), median in sorted(now.items()):
        if stat != "median" or (variable, horizon, "median") not in before:
            continue
        def width(vals, lo, hi):
            return vals[(variable, horizon, hi)] - vals[(variable, horizon, lo)]
        rows.append({
            "variable": variable, "horizon": horizon, "median": median,
            "previous_median": before[(variable, horizon, "median")],
            "median_change": round(median - before[(variable, horizon, "median")], 3),
            "ct_width_change": round(width(now, "ct_low", "ct_high") - width(before, "ct_low", "ct_high"), 3),
            "range_width_change": round(width(now, "range_low", "range_high") - width(before, "range_low", "range_high"), 3),
        })
    year = str(latest.year)
    by_key = {(r["variable"], r["horizon"]): r["median_change"] for r in rows}
    infl, funds = by_key.get(("pce_inflation", year)), by_key.get(("federal_funds_rate", year))
    return {"bank": BANK, "round": latest, "previous_round": previous, "revisions": rows,
            "reaction_function": {"flag": reaction_flag(infl, funds), "inflation_revision": infl,
                                  "funds_rate_revision": funds, "horizon": year,
                                  "rule": "PCE inflation median up ≥ 0.2pp: funds median unchanged → tolerance; up → response"}}
