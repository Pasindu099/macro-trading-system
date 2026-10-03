"""CFTC TFF positioning metrics: net, percentiles, crowding, squeeze watch, extremes.

All alignment with spot uses the COT report_date (Tuesday position date), never the
Friday release date. Spot on a report_date is the newest valid close on or before it.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services.dxy import DXY_PAIR

PAIRS_CONFIG = Path("config/pairs.yaml")
CURRENCIES = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]
CATEGORIES = ["dealer", "asset_manager", "leveraged_funds", "other_reportable", "nonreportable"]
CROWDING_CATEGORIES = ["leveraged_funds", "asset_manager"]
LOOKBACK_YEARS = {"1y": 1, "3y": 3, "5y": 5}
CROWDED_LONG, CROWDED_SHORT = 85.0, 15.0
# Spot pair for each currency vs USD, and whether a rising pair means the currency rises.
USD_PAIRS = {
    "EUR": ("EUR/USD", 1), "GBP": ("GBP/USD", 1), "AUD": ("AUD/USD", 1), "NZD": ("NZD/USD", 1),
    "CAD": ("USD/CAD", -1), "CHF": ("USD/CHF", -1), "JPY": ("USD/JPY", -1),
}
# Extreme bands on the leveraged-funds 3y percentile, with the crowd direction they imply.
EXTREME_BANDS = {
    ">90": (lambda p: p > 90, 1),
    "85-90": (lambda p: 85 <= p <= 90, 1),
    "10-15": (lambda p: 10 <= p <= 15, -1),
    "<10": (lambda p: p < 10, -1),
}
EXTREME_HORIZON_WEEKS = 8
SPOT_MAX_STALENESS = timedelta(days=5)


@dataclass(slots=True)
class Week:
    report_date: date
    long: int
    short: int
    open_interest: int

    @property
    def net(self) -> int:
        return self.long - self.short

    @property
    def net_pct_oi(self) -> float | None:
        return round(self.net / self.open_interest * 100, 2) if self.open_interest else None


# ── Pure calculations ────────────────────────────────────────────────

def percentile_rank(window: list[float], value: float) -> float | None:
    """0 = lowest (most short) in the window, 100 = highest (most long); ties share the midpoint."""
    if len(window) < 2:
        return None
    below = sum(1 for v in window if v < value)
    equal = sum(1 for v in window if v == value)
    return round((below + 0.5 * (equal - 1)) / (len(window) - 1) * 100, 2)


def lookback_start(as_of: date, years: int) -> date:
    return as_of - timedelta(days=round(365.25 * years))


def rolling_percentiles(dates: list[date], values: list[float], years: int) -> list[float | None]:
    """Percentile of each value within the trailing `years` window ending on (and including) its date.

    None until the window holds 90% of its weeks, so early history cannot fake extremes.
    """
    min_weeks = int(52 * years * 0.9)
    output = []
    for i, as_of in enumerate(dates):
        start = bisect.bisect_right(dates, lookback_start(as_of, years))
        window = values[start:i + 1]
        output.append(percentile_rank(window, values[i]) if len(window) >= min_weeks else None)
    return output


def crowding_label(percentile: float | None) -> str | None:
    if percentile is None:
        return None
    if percentile >= CROWDED_LONG:
        return "crowded_long"
    if percentile <= CROWDED_SHORT:
        return "crowded_short"
    return "neutral"


def squeeze_status(percentile: float | None, nets: list[int], spot_move_2w: float | None) -> dict[str, Any]:
    """Squeeze watch from the crowd direction, 2-week currency move and the last 3 weekly nets.

    high: crowded + spot against the crowd + net reduced 2 consecutive weeks;
    medium: crowded + exactly one of those; trend_confirming: crowded and spot still with the crowd.
    """
    label = crowding_label(percentile)
    if label not in {"crowded_long", "crowded_short"}:
        return {"status": "none", "crowding": label, "against_crowd": None, "reducing": None}
    direction = 1 if label == "crowded_long" else -1
    against = spot_move_2w is not None and direction * spot_move_2w < 0
    reducing = len(nets) >= 3 and direction * (nets[-1] - nets[-2]) < 0 and direction * (nets[-2] - nets[-3]) < 0
    if against and reducing:
        status = "high"
    elif against or reducing:
        status = "medium"
    elif spot_move_2w is not None and direction * spot_move_2w > 0:
        status = "trend_confirming"
    else:
        status = "none"
    return {"status": status, "crowding": label, "against_crowd": against, "reducing": reducing}


def find_episodes(percentiles: list[float | None], in_band, horizon: int = EXTREME_HORIZON_WEEKS) -> list[int]:
    """Indices of first weeks entering a band; a new episode cannot start within `horizon` weeks of the last."""
    episodes: list[int] = []
    previous_in = False
    for i, p in enumerate(percentiles):
        now_in = p is not None and in_band(p)
        if now_in and not previous_in and (not episodes or i - episodes[-1] >= horizon):
            episodes.append(i)
        previous_in = now_in
    return episodes


def pct_move(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start == 0:
        return None
    return (end / start - 1) * 100


# ── Spot alignment ────────────────────────────────────────────────────

class SpotSeries:
    """Currency value vs USD (rising = currency strengthens), read on report dates."""

    def __init__(self, points: list[tuple[date, float]]):
        points = sorted(points)
        self.dates = [d for d, _ in points]
        self.values = [v for _, v in points]

    def on(self, report_date: date) -> float | None:
        i = bisect.bisect_right(self.dates, report_date) - 1
        if i < 0 or report_date - self.dates[i] > SPOT_MAX_STALENESS:
            return None
        return self.values[i]


# ── Loaders ───────────────────────────────────────────────────────────

async def load_positions(currencies: list[str] | None = None) -> dict[str, dict[str, list[Week]]]:
    query = text("""
        SELECT currency, category, report_date, long, short, open_interest
        FROM cot_positions
        WHERE (CAST(:currencies AS text[]) IS NULL OR currency = ANY(CAST(:currencies AS text[])))
        ORDER BY currency, category, report_date
    """)
    async with get_sessionmaker()() as session:
        rows = (await session.execute(query, {"currencies": currencies})).all()
    output: dict[str, dict[str, list[Week]]] = {}
    for r in rows:
        output.setdefault(r.currency, {}).setdefault(r.category, []).append(
            Week(r.report_date, int(r.long), int(r.short), int(r.open_interest)))
    return output


async def load_spot() -> dict[str, SpotSeries]:
    query = text("""
        SELECT DISTINCT ON (pair, observation_date) pair, observation_date, close_value::float AS close
        FROM fx_spot_observations
        WHERE pair = ANY(:pairs) AND quality_status = 'valid' AND NOT is_outlier
        ORDER BY pair, observation_date, ingested_at DESC, id DESC
    """)
    async with get_sessionmaker()() as session:
        rows = (await session.execute(query, {"pairs": [p for p, _ in USD_PAIRS.values()] + [DXY_PAIR]})).all()
    by_pair: dict[str, list[tuple[date, float]]] = {}
    for r in rows:
        by_pair.setdefault(r.pair, []).append((r.observation_date, r.close))
    series = {
        ccy: SpotSeries([(d, v if sign > 0 else 1 / v) for d, v in by_pair.get(pair, [])])
        for ccy, (pair, sign) in USD_PAIRS.items()
    }
    # USD = ICE Dollar Index computed from its six components (rising = USD strengthens).
    series["USD"] = SpotSeries(by_pair.get(DXY_PAIR, []))
    return series


# ── Per-currency summaries ───────────────────────────────────────────

def _change(weeks: list[Week], n: int) -> int | None:
    return weeks[-1].net - weeks[-1 - n].net if len(weeks) > n else None


def category_snapshot(weeks: list[Week], years: int) -> dict[str, Any]:
    dates, nets = [w.report_date for w in weeks], [w.net for w in weeks]
    latest = weeks[-1]
    start = bisect.bisect_right(dates, lookback_start(latest.report_date, years))
    percentile = percentile_rank(nets[start:], latest.net)
    return {
        "report_date": latest.report_date.isoformat(), "long": latest.long, "short": latest.short,
        "net": latest.net, "net_pct_oi": latest.net_pct_oi, "open_interest": latest.open_interest,
        "percentile": percentile, "lookback_weeks": len(nets) - start,
        "change_1w": _change(weeks, 1), "change_4w": _change(weeks, 4),
    }


def _years(lookback: str) -> int:
    if lookback not in LOOKBACK_YEARS:
        raise ValueError(f"lookback must be one of {sorted(LOOKBACK_YEARS)}")
    return LOOKBACK_YEARS[lookback]


def _currency(currency: str) -> str:
    ccy = currency.upper()
    if ccy not in CURRENCIES:
        raise ValueError(f"Unsupported currency: {currency}")
    return ccy


async def get_crowding(lookback: str = "3y") -> dict[str, Any]:
    years = _years(lookback)
    positions = await load_positions()
    rows = []
    for ccy in CURRENCIES:
        by_cat = positions.get(ccy, {})
        entry: dict[str, Any] = {"currency": ccy}
        for category in CROWDING_CATEGORIES:
            weeks = by_cat.get(category)
            if not weeks:
                entry[category] = {"status": "unavailable", "reason": "no COT history"}
                continue
            snap = category_snapshot(weeks, years)
            entry[category] = {**snap, "crowding": crowding_label(snap["percentile"])}
        rows.append(entry)
    return {"lookback": lookback, "thresholds": {"crowded_long": CROWDED_LONG, "crowded_short": CROWDED_SHORT},
            "currencies": rows}


async def get_currency_positioning(currency: str, weeks: int = 52) -> dict[str, Any]:
    ccy = _currency(currency)
    by_cat = (await load_positions([ccy])).get(ccy, {})
    if not by_cat:
        return {"currency": ccy, "status": "unavailable", "reason": "no COT history"}
    categories = {}
    for category in CATEGORIES:
        series = by_cat.get(category, [])
        if not series:
            continue
        dates, nets = [w.report_date for w in series], [float(w.net) for w in series]
        pctl = {name: rolling_percentiles(dates, nets, yrs) for name, yrs in LOOKBACK_YEARS.items()}
        start = max(0, len(series) - weeks)
        categories[category] = {
            "latest": {**category_snapshot(series, 3),
                       "percentiles": {name: values[-1] for name, values in pctl.items()}},
            "history": [
                {"report_date": w.report_date.isoformat(), "net": w.net, "net_pct_oi": w.net_pct_oi,
                 "open_interest": w.open_interest, **{f"pctl_{n}": pctl[n][i] for n in LOOKBACK_YEARS}}
                for i, w in enumerate(series) if i >= start
            ],
        }
    return {"currency": ccy, "weeks": weeks, "categories": categories}


async def get_flows(window: str = "1W") -> dict[str, Any]:
    n = {"1W": 1, "4W": 4}.get(window)
    if n is None:
        raise ValueError("window must be 1W or 4W")
    positions = await load_positions()
    rows = []
    for ccy in CURRENCIES:
        entry: dict[str, Any] = {"currency": ccy}
        for category in CATEGORIES:
            weeks = positions.get(ccy, {}).get(category, [])
            change = _change(weeks, n)
            oi = weeks[-1].open_interest if weeks else 0
            entry[category] = {
                "net": weeks[-1].net if weeks else None, "change": change,
                "change_pct_oi": round(change / oi * 100, 2) if change is not None and oi else None,
            }
        entry["report_date"] = next((w[-1].report_date.isoformat() for w in positions.get(ccy, {}).values() if w), None)
        rows.append(entry)
    return {"window": window, "currencies": rows}


async def get_squeeze(lookback: str = "3y") -> dict[str, Any]:
    years = _years(lookback)
    positions = await load_positions()
    spot = await load_spot()
    rows = []
    for ccy in CURRENCIES:
        entry: dict[str, Any] = {"currency": ccy}
        for category in CROWDING_CATEGORIES:
            weeks = positions.get(ccy, {}).get(category, [])
            if len(weeks) < 3:
                entry[category] = {"status": "unavailable", "reason": "fewer than 3 weeks of COT history"}
                continue
            snap = category_snapshot(weeks, years)
            series = spot.get(ccy)
            move = pct_move(series.on(weeks[-3].report_date), series.on(weeks[-1].report_date)) if series else None
            entry[category] = {
                **squeeze_status(snap["percentile"], [w.net for w in weeks[-3:]], move),
                "percentile": snap["percentile"], "report_date": snap["report_date"],
                "spot_move_2w_pct": round(move, 3) if move is not None else None,
                "net_last_3w": [w.net for w in weeks[-3:]],
            }
        rows.append(entry)
    return {"lookback": lookback, "spot_basis": "currency vs USD; USD = computed ICE Dollar Index (DXY)",
            "currencies": rows}


def extreme_band_stats(weeks: list[Week], spot: SpotSeries | None) -> dict[str, Any]:
    dates = [w.report_date for w in weeks]
    pctl = rolling_percentiles(dates, [float(w.net) for w in weeks], 3)
    bands = {}
    for band, (in_band, direction) in EXTREME_BANDS.items():
        episodes = find_episodes(pctl, in_band)
        moves4, moves8, adverse, against_count = [], [], [], 0
        for i in episodes:
            start = spot.on(dates[i]) if spot else None
            m4 = pct_move(start, spot.on(dates[i + 4])) if spot and i + 4 < len(dates) else None
            m8 = pct_move(start, spot.on(dates[i + 8])) if spot and i + 8 < len(dates) else None
            if m4 is not None:
                moves4.append(m4)
            if m8 is not None:
                moves8.append(m8)
                against_count += direction * m8 < 0
                adverse.append(max_adverse_move(spot, dates[i], dates[i + 8], direction))
        bands[band] = {
            "episodes": len(episodes),
            "episode_dates": [dates[i].isoformat() for i in episodes],
            "avg_move_4w_pct": _avg(moves4),
            "avg_move_8w_pct": _avg(moves8),
            "against_crowd_after_8w": round(against_count / len(moves8) * 100, 1) if moves8 else None,
            "max_adverse_move_8w": _avg([a for a in adverse if a is not None]),
            "episodes_with_8w": len(moves8),
        }
    return bands


def max_adverse_move(spot: SpotSeries, start: date, end: date, direction: int) -> float | None:
    """Largest move against the crowd (positive %, 0 if never against) on daily closes in (start, end]."""
    base = spot.on(start)
    if base is None:
        return None
    lo, hi = bisect.bisect_right(spot.dates, start), bisect.bisect_right(spot.dates, end)
    moves = [-direction * pct_move(base, v) for v in spot.values[lo:hi]]
    return max([0.0, *moves])


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


async def get_extremes(currency: str) -> dict[str, Any]:
    ccy = _currency(currency)
    weeks = (await load_positions([ccy])).get(ccy, {}).get("leveraged_funds", [])
    if not weeks:
        return {"currency": ccy, "status": "unavailable", "reason": "no COT history"}
    spot = (await load_spot()).get(ccy)
    return {
        "currency": ccy, "category": "leveraged_funds", "lookback": "3y",
        "horizon_weeks": EXTREME_HORIZON_WEEKS,
        "against_crowd_rule": "share of episodes whose 8W currency move is against the band's crowd",
        "max_adverse_rule": "largest daily-close move against the crowd within 8W, averaged per band (%)",
        "bands": extreme_band_stats(weeks, spot),
    }


def normalize_pair(pair: str) -> tuple[str, str]:
    name = pair.upper().replace("/", "").replace("-", "")
    config = yaml.safe_load(PAIRS_CONFIG.read_text(encoding="utf-8"))
    for item in config["pairs"]:
        if item["base"] + item["quote"] == name:
            return item["base"], item["quote"]
    raise ValueError(f"Unsupported pair: {pair}")


async def get_pair_positioning(pair: str, lookback: str = "3y") -> dict[str, Any]:
    base, quote = normalize_pair(pair)
    years = _years(lookback)
    positions = await load_positions([base, quote])
    legs = {}
    for ccy in (base, quote):
        weeks = positions.get(ccy, {}).get("leveraged_funds", [])
        legs[ccy] = category_snapshot(weeks, years) if weeks else None
    if not all(legs.values()):
        return {"pair": f"{base}/{quote}", "status": "unavailable", "reason": "missing COT history for a leg"}
    implied = round(legs[base]["percentile"] - legs[quote]["percentile"], 2)
    return {
        "pair": f"{base}/{quote}", "category": "leveraged_funds", "lookback": lookback,
        "implied_percentile_spread": implied,
        "basis": "base minus quote leveraged-funds percentile; no cross-currency futures exist",
        "base": {"currency": base, **legs[base]}, "quote": {"currency": quote, **legs[quote]},
    }
