"""CFTC TFF positioning metrics: net, percentiles, crowding, squeeze watch, extremes.

All alignment with spot uses the COT report_date (Tuesday position date), never the
Friday release date. Spot on a report_date is the newest valid close on or before it.
"""

from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services.dxy import DXY_PAIR

PAIRS_CONFIG = Path("config/pairs.yaml")
CURRENCIES = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]
CATEGORIES = ["dealer", "asset_manager", "leveraged_funds", "other_reportable", "nonreportable"]
CROWDING_CATEGORIES = ["leveraged_funds", "asset_manager"]
LOOKBACK_YEARS = {"52w": 1, "1y": 1, "3y": 3, "5y": 5}
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


def squeeze_status(percentile: float | None, nets: list[int], spot_move_2w: float | None,
                   typical_move: float | None = None) -> dict[str, Any]:
    """Squeeze watch from the crowd direction, 2-week currency move and the last 3 weekly nets.

    high: crowded + spot against the crowd + net reduced 2 consecutive weeks;
    medium: crowded + exactly one ingredient; working: neither ingredient meets the threshold.
    """
    label = crowding_label(percentile)
    if label not in {"crowded_long", "crowded_short"}:
        return {"status": "none", "crowding": label, "against_crowd": None, "reducing": None}
    direction = 1 if label == "crowded_long" else -1
    against = (direction * spot_move_2w < 0 and abs(spot_move_2w) >= .5 * typical_move
               if spot_move_2w is not None and typical_move is not None else None)
    reducing = len(nets) >= 3 and direction * (nets[-1] - nets[-2]) < 0 and direction * (nets[-2] - nets[-3]) < 0
    if against and reducing:
        status = "high"
    elif against or reducing:
        status = "medium"
    else:
        status = "working"
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
    from app.services.positioning_values import notional
    spot = (await load_spot()).get(ccy)
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
                       "net_usd_bn": notional(ccy, series[-1], spot)/1e9 if notional(ccy, series[-1], spot) is not None else None,
                       "percentiles": {name: values[-1] for name, values in pctl.items()}},
            "history": [
                {"report_date": w.report_date.isoformat(), "net": w.net, "net_pct_oi": w.net_pct_oi,
                 "long": w.long, "short": w.short,
                 "net_usd_bn": notional(ccy,w,spot)/1e9 if notional(ccy,w,spot) is not None else None,
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
            price_date = (series.dates[bisect.bisect_right(series.dates, date.today())-1]
                          if series and series.on(date.today()) is not None else None)
            move = pct_move(series.on(price_date-timedelta(days=14)), series.on(price_date)) if price_date else None
            start = lookback_start(price_date or weeks[-1].report_date, years)
            returns = [pct_move(series.on(day-timedelta(days=14)), series.on(day))
                       for day in series.dates if price_date and start <= day <= price_date] if series else []
            returns = [v for v in returns if v is not None]
            typical = statistics.stdev(returns) if len(returns) >= 2 else None
            consecutive = (weeks[-1].report_date-weeks[-2].report_date).days == 7 and (weeks[-2].report_date-weeks[-3].report_date).days == 7
            status = squeeze_status(snap["percentile"], [w.net for w in weeks[-3:]] if consecutive else [], move, typical)
            if not consecutive:
                status["reducing"] = None
            entry[category] = {
                **status, "typical_2w_move_pct": typical, "price_date": str(price_date) if price_date else None,
                "percentile": snap["percentile"], "report_date": snap["report_date"],
                "spot_move_2w_pct": move,
                "net_last_3w": [w.net for w in weeks[-3:]],
                "open_interest_last_3w": [w.open_interest for w in weeks[-3:]],
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
            "median_move_4w_pct": statistics.median(moves4) if moves4 else None,
            "median_move_8w_pct": statistics.median(moves8) if moves8 else None,
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


async def get_pair_positioning(pair: str, lookback: str = "3y", weeks: int = 156) -> dict[str, Any]:
    base, quote = normalize_pair(pair)
    years = _years(lookback)
    positions = await load_positions([base, quote])
    legs = {}
    for ccy in (base, quote):
        leg_weeks = positions.get(ccy, {}).get("leveraged_funds", [])
        legs[ccy] = category_snapshot(leg_weeks, years) if leg_weeks else None
    from app.services.positioning_values import pair_series
    dollars = await pair_series(pair, weeks, positions=positions)
    implied = (round(legs[base]["percentile"] - legs[quote]["percentile"], 2)
               if all(legs.values()) and all(v["percentile"] is not None for v in legs.values()) else None)
    return {
        **dollars, "category": "leveraged_funds", "lookback": lookback,
        "implied_percentile_spread": implied,
        "basis": "base minus quote leveraged-funds percentile; no cross-currency futures exist",
        "base": {"currency": base, **(legs[base] or {})}, "quote": {"currency": quote, **(legs[quote] or {})},
    }


# ── Step 14 B: summary, release info, divergence, institutions vs retail ──

SCHEDULE_CONFIG = Path("config/cftc_release_schedule.yaml")
SQUEEZE_RANK = {"high": 3, "medium": 2, "working": 1, "none": 0}
DIVERGENCE_HIGH, DIVERGENCE_LOW = 75.0, 25.0
DIVERGENCE_MIN_STD = 0.5  # |4W net change| must be >= 0.5 x std of 4W changes over the lookback
IMPLIED_BIAS, RETAIL_SIDE = 20.0, 60.0


def ordinal(value: float) -> str:
    n = int(round(value))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def summary_sentence(squeeze_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The single most important leveraged-funds story: squeeze status first, then extremeness."""
    candidates = []
    for row in squeeze_rows:
        lf = row.get("leveraged_funds") or {}
        if lf.get("crowding") in {"crowded_long", "crowded_short"} and lf.get("percentile") is not None:
            candidates.append((SQUEEZE_RANK.get(lf["status"], 0), abs(lf["percentile"] - 50), row["currency"], lf))
    if not candidates:
        return {"currency": None, "status": "none",
                "text": "No currency is crowded among speculators: leveraged-funds positions sit between the "
                        f"{ordinal(CROWDED_SHORT)} and {ordinal(CROWDED_LONG)} percentiles."}
    _, _, ccy, lf = max(candidates, key=lambda item: item[:3])
    side = "long" if lf["crowding"] == "crowded_long" else "short"
    sentence = f"Speculators crowded {side} {ccy} ({ordinal(lf['percentile'])} percentile)"
    status = lf["status"]
    if status == "high":
        sentence += f" and {side}s are being cut: squeeze watch high."
    elif status == "medium" and lf.get("reducing"):
        sentence += f" and {side}s are being cut: squeeze watch medium."
    elif status == "medium":
        sentence += f" and {ccy} is moving against them: squeeze watch medium."
    elif status == "working":
        sentence += f" and the crowded bet is still working for them."
    else:
        sentence += "."
    return {"currency": ccy, "status": status, "percentile": lf["percentile"], "text": sentence}


def report_as_of(release: date) -> date:
    """Tuesday covered by a release: same week for Friday, previous week for a delayed Monday."""
    return release - timedelta(days=(release.weekday() - 1) % 7)


def _schedule_config() -> dict[str, Any]:
    return yaml.safe_load(SCHEDULE_CONFIG.read_text(encoding="utf-8"))


def release_schedule() -> list[dict[str, Any]]:
    rows = []
    for entries in _schedule_config()["releases"].values():
        for entry in entries:
            release = entry["date"] if isinstance(entry, dict) else entry
            rows.append({"release_date": release, "as_of": report_as_of(release),
                         "delayed": bool(isinstance(entry, dict) and entry.get("delayed"))})
    return sorted(rows, key=lambda row: row["release_date"])


def release_info(latest_report: date | None, now: datetime | None = None) -> dict[str, Any]:
    """Data as-of (Tuesday), its release date (Friday or holiday shift) and the next scheduled release."""
    now = now or datetime.now(UTC)
    schedule = release_schedule()
    hour, minute = map(int, _schedule_config()["release_time_et"].split(":"))
    eastern = ZoneInfo("America/New_York")

    def released_at(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=eastern)

    current = next((row for row in schedule if row["as_of"] == latest_report), None) if latest_report else None
    upcoming = next((row for row in schedule if released_at(row["release_date"]) > now), None)
    if current:
        released, released_source = current["release_date"].isoformat(), "cftc_schedule"
    elif latest_report:
        released, released_source = (latest_report + timedelta(days=3)).isoformat(), "assumed_friday"
    else:
        released, released_source = None, None
    return {
        "as_of": latest_report.isoformat() if latest_report else None,
        "released": released, "released_source": released_source,
        "next_release": upcoming["release_date"].isoformat() if upcoming else None,
        "next_as_of": upcoming["as_of"].isoformat() if upcoming else None,
        "next_delayed": upcoming["delayed"] if upcoming else None,
        "next_release_status": "scheduled" if upcoming else "schedule not yet published",
        "release_time_et": f"{hour:02d}:{minute:02d}",
    }


def change_4w_std(weeks: list[Week], years: int) -> float | None:
    """Standard deviation of 4-week net changes ending inside the lookback window."""
    if len(weeks) < 6:
        return None
    start = lookback_start(weeks[-1].report_date, years)
    changes = [weeks[i].net - weeks[i - 4].net for i in range(4, len(weeks)) if weeks[i].report_date > start]
    return statistics.stdev(changes) if len(changes) >= 2 else None


def divergence_flag(percentile: float | None, net_change_4w: int | None, price_move_4w: float | None,
                    change_std: float | None = None) -> dict[str, Any]:
    """'divergence' when the 4-week currency move and the leveraged-funds net change point opposite ways,
    the net change is material (>= 0.5 x std of 4-week changes) and positioning is >= 75th / <= 25th."""
    if percentile is None or net_change_4w is None or price_move_4w is None or change_std is None:
        return {"flag": "pending", "reason": "missing percentile, 4-week net change, its std or price"}
    disagree = (net_change_4w > 0 > price_move_4w) or (net_change_4w < 0 < price_move_4w)
    material = abs(net_change_4w) >= DIVERGENCE_MIN_STD * change_std
    extreme = percentile >= DIVERGENCE_HIGH or percentile <= DIVERGENCE_LOW
    return {"flag": "divergence" if disagree and material and extreme else "none",
            "disagree": disagree, "material": material, "extreme": extreme,
            "min_change": round(DIVERGENCE_MIN_STD * change_std)}


async def get_divergence(lookback: str = "3y") -> dict[str, Any]:
    years = _years(lookback)
    positions = await load_positions()
    spot = await load_spot()
    rows = []
    for ccy in CURRENCIES:
        weeks = positions.get(ccy, {}).get("leveraged_funds", [])
        if len(weeks) < 5:
            rows.append({"currency": ccy, "flag": "pending", "reason": "fewer than 5 weeks of COT history"})
            continue
        snap = category_snapshot(weeks, years)
        series = spot.get(ccy)
        move = pct_move(series.on(weeks[-5].report_date), series.on(weeks[-1].report_date)) if series else None
        rows.append({"currency": ccy, "percentile": snap["percentile"], "net_change_4w": snap["change_4w"],
                     "price_move_4w_pct": round(move, 3) if move is not None else None,
                     "report_date": snap["report_date"],
                     **divergence_flag(snap["percentile"], snap["change_4w"], move, change_4w_std(weeks, years))})
    return {"lookback": lookback, "window_weeks": 4,
            "rule": ("4W price direction vs 4W leveraged-funds net change disagree, |net change| >= 0.5 x std of "
                     "4W changes over the lookback, and percentile >= 75 or <= 25"),
            "currencies": rows}


def retail_signal(implied: float | None, retail_long_pct: float | None) -> str:
    """'opposite sides' / 'same side' / 'no signal' for COT-implied bias vs Myfxbook % long."""
    if implied is None or retail_long_pct is None:
        return "no signal"
    retail_short = 100 - retail_long_pct
    if implied > IMPLIED_BIAS:
        if retail_short > RETAIL_SIDE:
            return "opposite sides"
        return "same side" if retail_long_pct > RETAIL_SIDE else "no signal"
    if implied < -IMPLIED_BIAS:
        if retail_long_pct > RETAIL_SIDE:
            return "opposite sides"
        return "same side" if retail_short > RETAIL_SIDE else "no signal"
    return "no signal"


def config_pairs() -> list[tuple[str, str]]:
    config = yaml.safe_load(PAIRS_CONFIG.read_text(encoding="utf-8"))
    return [(item["base"], item["quote"]) for item in config["pairs"]]


async def get_institutions_vs_retail(lookback: str = "3y") -> dict[str, Any]:
    from app.services import myfxbook

    years = _years(lookback)
    positions = await load_positions()
    pctl = {}
    for ccy in CURRENCIES:
        weeks = positions.get(ccy, {}).get("leveraged_funds", [])
        pctl[ccy] = category_snapshot(weeks, years)["percentile"] if weeks else None
    retail = {row["pair"]: row for row in await myfxbook.latest()}
    configured = myfxbook.credentials() is not None
    rows = []
    for base, quote in config_pairs():
        implied = (round(pctl[base] - pctl[quote], 2)
                   if pctl.get(base) is not None and pctl.get(quote) is not None else None)
        snap = retail.get(base + quote)
        long_pct = float(snap["long_pct"]) if snap else None
        if implied is None:
            side = None
        else:
            side = "long-biased" if implied > IMPLIED_BIAS else "short-biased" if implied < -IMPLIED_BIAS else "neutral"
        rows.append({"pair": f"{base}/{quote}", "cot_implied": implied, "cot_side": side,
                     "retail_long_pct": long_pct,
                     "retail_taken_at": snap["taken_at"].isoformat() if snap else None,
                     "signal": retail_signal(implied, long_pct)})
    if not configured:
        state, message = "not_configured", myfxbook.NOT_CONFIGURED
    elif not retail:
        state, message = "pending", "No Myfxbook snapshot stored yet."
    else:
        state, message = "ok", None
    return {"lookback": lookback, "retail_source": "Myfxbook community outlook", "retail_state": state,
            "retail_message": message,
            "rule": "opposite sides: COT-implied > +20 and retail > 60% short, or < -20 and retail > 60% long",
            "pairs": rows}


async def get_summary(lookback: str = "3y") -> dict[str, Any]:
    squeeze = await get_squeeze(lookback)
    latest = max((date.fromisoformat(row["leveraged_funds"]["report_date"]) for row in squeeze["currencies"]
                  if (row.get("leveraged_funds") or {}).get("report_date")), default=None)
    return {"lookback": lookback, "summary": summary_sentence(squeeze["currencies"]),
            "release": release_info(latest)}
