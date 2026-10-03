"""Fed regime model v1, transition checklists, and the SEP-vs-market gap."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services import cb_tracking
from app.services.event_innovation_feed import FeedFilters, build_event_innovation_feed
from app.services.fred import load_series
from app.services.meeting_calendar import get_upcoming_meetings
from app.services.rate_probability import (
    DATA_STATE_LIVE, _bank_config, _parse_dt, compute_meeting_probabilities, step_path_rate,
)

BANK = "FED"
MOVE_WINDOW = timedelta(days=182)        # "last move within 6 months"
NEAR_ZERO = 0.5
REGIMES = ["qe", "near_zero", "cutting", "holding", "hiking"]


# ── Regime ─────────────────────────────────────────────────────────────

def classify_regime(decisions: list[tuple[date, float]], today: date) -> dict[str, Any]:
    """decisions: (date, rate) in time order. Last move = latest decision that changed the rate."""
    if not decisions:
        return {"regime": "unavailable", "reason": "No policy-rate decisions stored"}
    rate = decisions[-1][1]
    last_move = None
    for (d0, r0), (d1, r1) in zip(decisions, decisions[1:]):
        if r1 != r0:
            last_move = (d1, r1 - r0)
    base = {"rate": rate, "last_move_date": last_move[0] if last_move else None,
            "last_move_bp": round(last_move[1] * 100) if last_move else None,
            "qe": {"status": "unavailable", "reason": "Needs balance-sheet data"}}
    if rate <= NEAR_ZERO:
        return {**base, "regime": "near_zero"}
    if last_move and today - last_move[0] <= MOVE_WINDOW:
        return {**base, "regime": "hiking" if last_move[1] > 0 else "cutting"}
    return {**base, "regime": "holding"}


def sahm_rule(unrate: list[tuple[date, float]]) -> dict[str, Any] | None:
    """3-month average unemployment minus its minimum over the prior 12 months; triggered at ≥ 0.5pp."""
    values = [v for _, v in unrate]
    if len(values) < 15:
        return None
    avg3 = [sum(values[i - 2:i + 1]) / 3 for i in range(2, len(values))]
    current = avg3[-1]
    prior_min = min(avg3[-13:-1])
    gap = current - prior_min
    return {"value": round(gap, 2) + 0.0, "triggered": gap >= 0.5, "as_of": unrate[-1][0]}  # +0.0 avoids "-0.0"


def _condition(name: str, met: bool | None, value: Any = None, threshold: str = "", detail: str = "",
               reason: str | None = None) -> dict[str, Any]:
    status = "unavailable" if met is None else ("met" if met else "not_met")
    return {"name": name, "status": status, "met": met, "value": value, "threshold": threshold,
            "detail": detail, **({"reason": reason} if reason else {})}


def _change_over(points: list[tuple[date, float]], days: int) -> float | None:
    if not points:
        return None
    end_day, end = points[-1]
    earlier = [v for d, v in points if d <= end_day - timedelta(days=days)]
    return end - earlier[-1] if earlier else None


# ── Data access ────────────────────────────────────────────────────────

async def policy_decisions() -> list[tuple[date, float]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT DISTINCT ON (r.released_at::date) r.released_at::date AS d, r.actual::float AS rate
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = 'US' AND i.canonical_name = 'fed_interest_rate_decision'
              AND r.actual IS NOT NULL AND r.released_at <= now()
            ORDER BY r.released_at::date, r.retrieved_at DESC, r.id DESC
        """))
        return [(r.d, r.rate) for r in rows]


async def ism_manufacturing(months: int = 6) -> list[tuple[date, float]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT DISTINCT ON (coalesce(r.period_start_date, r.released_at::date))
                   coalesce(r.period_start_date, r.released_at::date) AS d, r.actual::float AS v
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = 'US' AND i.canonical_name = 'ism_manufacturing_pmi'
              AND r.actual IS NOT NULL AND r.released_at <= now()
            ORDER BY coalesce(r.period_start_date, r.released_at::date), r.released_at DESC, r.id DESC
        """))
        return [(r.d, r.v) for r in rows][-months:]


async def labor_surprises(days: int = 56) -> list[float]:
    async with get_sessionmaker()() as session:
        feed = await build_event_innovation_feed(
            session, FeedFilters(days=days, include_unscored=False, country_code="US", category="Labor"))
    return [row["initial"] for row in feed["rows"]]


# ── Assembly ───────────────────────────────────────────────────────────

async def get_regime(today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    regime = classify_regime(await policy_decisions(), today)
    core = await load_series("PCEPILFE", today - timedelta(days=800))
    unrate = await load_series("UNRATE", today - timedelta(days=900))
    hy = await load_series("BAMLH0A0HYM2", today - timedelta(days=200))
    core_3m = cb_tracking.annualised_3m([v for _, v in core])
    surprises = await labor_surprises()
    hy_change = _change_over(hy, 91)
    sahm = sahm_rule(unrate)
    ism = await ism_manufacturing(3)
    rate = regime.get("rate")

    hiking_to_holding = [
        _condition("Core PCE 3m annualised below 3%", None if core_3m is None else core_3m < 3.0,
                   round(core_3m, 2) if core_3m is not None else None, "< 3.0%",
                   f"PCEPILFE to {core[-1][0]:%b %Y}" if core else "", None if core_3m is not None else "No PCEPILFE data"),
        _condition("Labor surprises negative over 8 weeks",
                   None if not surprises else sum(surprises) / len(surprises) < 0,
                   round(sum(surprises) / len(surprises), 2) if surprises else None, "mean surprise < 0σ",
                   f"{len(surprises)} scored US labor releases (Event Innovation)",
                   None if surprises else "No scored US labor releases in 8 weeks"),
        _condition("Financial conditions tightening", None if hy_change is None else hy_change * 100 > 50,
                   round(hy_change * 100) if hy_change is not None else None, "HY OAS up > 50bp in 3 months",
                   "FRED BAMLH0A0HYM2", None if hy_change is not None else "Not enough HY spread history"),
        _condition("Policy judged restrictive by a majority", None, reason="Needs the speaker pipeline"),
    ]
    toward_cutting = [
        _condition("Sahm rule triggered", None if sahm is None else sahm["triggered"],
                   sahm["value"] if sahm else None, "≥ 0.5pp", "UNRATE 3m avg vs prior 12m low",
                   None if sahm else "Not enough UNRATE history"),
        _condition("ISM manufacturing below 50 for 3+ months",
                   None if len(ism) < 3 else all(v < 50 for _, v in ism),
                   [v for _, v in ism] or None, "< 50 for the last 3 months", "ism_manufacturing_pmi",
                   None if len(ism) >= 3 else "Fewer than 3 ISM manufacturing prints"),
        _condition("HY credit spread above 600bp", None if not hy else hy[-1][1] * 100 > 600,
                   round(hy[-1][1] * 100) if hy else None, "> 600bp", "FRED BAMLH0A0HYM2",
                   None if hy else "No HY spread data"),
        _condition("Core PCE 3m annualised below 2%", None if core_3m is None else core_3m < 2.0,
                   round(core_3m, 2) if core_3m is not None else None, "< 2.0%", "PCEPILFE",
                   None if core_3m is not None else "No PCEPILFE data"),
        _condition("Policy rate at or below 0.5%", None if rate is None else rate <= NEAR_ZERO,
                   rate, "≤ 0.5%", "Latest FOMC decision", None if rate is not None else "No decision stored"),
    ]

    def score(conds):
        avail = [c for c in conds if c["met"] is not None]
        return {"met": sum(1 for c in avail if c["met"]), "available": len(avail), "total": len(conds)}

    return {"bank": BANK, "as_of": today, **regime, "ladder": REGIMES, "transitions": [
        {"id": "hiking_to_holding", "title": "Hiking → Holding", "conditions": hiking_to_holding, "score": score(hiking_to_holding)},
        {"id": "toward_cutting", "title": "Toward cutting / QE", "conditions": toward_cutting, "score": score(toward_cutting)},
    ]}


def market_rate_at(meeting_rates: list[tuple[date, float]], start_rate: float, target: date,
                   uncovered_meetings: list[date]) -> float | None:
    """Market step path at `target`; None if a meeting the futures strip cannot price falls before it."""
    if any(m < target for m in uncovered_meetings):
        return None
    return step_path_rate(meeting_rates, start_rate, target)


def compute_gap(sep_medians: dict[str, float], meeting_rates: list[tuple[date, float]], start_rate: float,
                uncovered_meetings: list[date]) -> list[dict[str, Any]]:
    """SEP year-end median vs market rate on 31 Dec of the same year (identical dates on both sides)."""
    rows = []
    for horizon, median in sorted(sep_medians.items()):
        if not horizon.isdigit():
            continue
        target = date(int(horizon), 12, 31)
        market = market_rate_at(meeting_rates, start_rate, target, uncovered_meetings)
        rows.append({"horizon": horizon, "date": target, "sep_median": median,
                     "market": round(market, 3) if market is not None else None,
                     "gap_bp": round((median - market) * 100) if market is not None else None,
                     **({} if market is not None else {"reason": "Beyond the fed funds futures strip"})})
    return rows


async def get_gap() -> dict[str, Any]:
    rounds = await cb_tracking.sep_rounds()
    if not rounds:
        return {"status": "unavailable", "reason": "No SEP rounds stored"}
    latest = rounds[-1]
    values = await cb_tracking.round_values(latest)
    medians = {h: v for (var, h, stat), v in values.items() if var == "federal_funds_rate" and stat == "median"}
    adj = float(_bank_config(BANK)["rate_basis_adj"])
    async with get_sessionmaker()() as session:
        probabilities = await compute_meeting_probabilities(BANK, n_meetings=12, db_session=session)
        calendar = await get_upcoming_meetings(BANK, 24, session)
    live = [p for p in probabilities if p.data_state == DATA_STATE_LIVE]
    if not live:
        return {"status": "unavailable", "reason": "No live fed funds futures curve"}
    # The futures path is in EFFR terms (implied_rate minus the display basis), which tracks the target-range midpoint.
    meeting_rates = [(p.meeting_dt.date(), p.implied_rate - adj) for p in live]
    start = live[0].current_rate - adj
    # Meetings the futures strip cannot price (not returned as live) make later year-ends unavailable.
    priced = {p.meeting_dt.date() for p in live}
    uncovered = [d for d in (_parse_dt(m["meeting_dt"]).date() for m in calendar) if d not in priced]
    rows = compute_gap(medians, meeting_rates, start, uncovered)
    tracking = await cb_tracking.get_tracking()
    core = tracking.get("variables", {}).get("core_pce_inflation", {}).get("status")
    tilt = {"running_hot": "hawkish_risk", "running_cold": "dovish_risk"}.get(core, "neutral")
    return {"bank": BANK, "round": latest, "gaps": rows,
            "tilt": {"flag": tilt, "basis": f"core PCE tracking: {core}"},
            "method": ("Gap v1 (own-path bank): SEP median federal funds rate (midpoint of target range) at year end "
                       "minus the market-implied rate on 31 Dec of the same year, from the de-averaged fed funds "
                       "futures step path (EFFR terms). Positive = the Fed projects a higher rate than priced."),
            "market_source": "yfinance_ZQ"}
