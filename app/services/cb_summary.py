"""Per-bank Central Bank Terminal summary and blackout windows (Step 12 Part A)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services import ecb_pricing, ecb_regime, fed_regime
from app.services.fed_regime import classify_regime
from app.services.meeting_calendar import SUPPORTED_BANKS, get_upcoming_meetings
from app.services.rate_probability import DATA_STATE_LIVE, compute_meeting_probabilities

CONFIG_PATH = Path("config/central_banks.yaml")
MEETINGS_PATH = Path("config/cb_meetings.yaml")
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MAX_PRICED_MOVE_BP = 50
MAX_PRICING_AGE = timedelta(days=3)

# (country, canonical_name) of each bank's rate-decision history; 2Y yield country.
DECISIONS = {"FED": ("US", "fed_interest_rate_decision"), "ECB": ("EU", "ecb_deposit_rate"),
             "BOE": ("UK", "bank_rate"), "BOJ": ("JP", "boj_interest_rate_decision"),
             "RBA": ("AU", "cash_rate"), "BOC": ("CA", "overnight_rate"),
             "RBNZ": ("NZ", "official_cash_rate"), "SNB": ("CH", "policy_rate")}
TWO_YEAR = {"FED": "US", "ECB": "DE", "BOE": "UK", "BOJ": "JP", "RBA": "AU", "BOC": "CA", "RBNZ": "NZ", "SNB": "CH"}


@lru_cache(maxsize=1)
def bank_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["banks"]


# ── Blackout ───────────────────────────────────────────────────────────

def _business_days_before(day: date, n: int) -> date:
    while n:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def blackout_window(bank: str, decision: date) -> dict[str, Any] | None:
    """Start/end dates (inclusive) of the communication blackout around one decision day."""
    cfg = bank_config()[bank]
    rule = cfg.get("blackout") or {}
    start_rule, end_rule = rule.get("start"), rule.get("end")
    if not start_rule or not end_rule:
        return None
    first_day = decision - timedelta(days=int(cfg["meetings"].get("meeting_days", 1)) - 1)
    anchor = first_day if start_rule.get("anchor") == "first_day" else decision
    if "weekday" in start_rule:
        start = anchor - timedelta(days=1)
        seen = 0
        while True:
            if start.weekday() == WEEKDAYS[start_rule["weekday"]]:
                seen += 1
                if seen == int(start_rule.get("nth", 1)):
                    break
            start -= timedelta(days=1)
    elif "business_days_before" in start_rule:
        start = _business_days_before(anchor, int(start_rule["business_days_before"]))
    else:
        days = start_rule["days_before"]
        if decision.month in cfg["projections"].get("months", []) and "days_before_projection" in start_rule:
            days = start_rule["days_before_projection"]
        start = anchor - timedelta(days=int(days))
    end = decision + timedelta(days=int(end_rule.get("days_after_decision", 0)))
    return {"start": start, "end": end, "verified": bool(rule.get("verified")), "approx": bool(rule.get("approx"))}


def blackout_status(bank: str, decisions: list[date], today: date) -> dict[str, Any]:
    """Current blackout state from the next (or ongoing) decision day."""
    rule = bank_config()[bank].get("blackout") or {}
    if not rule.get("start"):
        return {"status": "unknown", "reason": "No published blackout rule", "verified": False}
    for decision in sorted(decisions):
        window = blackout_window(bank, decision)
        if window["end"] < today:
            continue
        state = "in_blackout" if window["start"] <= today else "open"
        return {"status": state, "decision": decision, **window,
                "days_until_start": max(0, (window["start"] - today).days)}
    return {"status": "unknown", "reason": "No upcoming meeting in calendar", "verified": rule.get("verified", False)}


# ── Summary pieces ─────────────────────────────────────────────────────

def summarize_decisions(rows: list[tuple[date, float | None]]) -> dict[str, Any]:
    """rows: (decision date, rate or None) in time order. A trailing None means the source omitted the value."""
    known = [(d, r) for d, r in rows if r is not None]
    if not known:
        return {"status": "unavailable", "reason": "No rate decisions stored"}
    latest_day = rows[-1][0]
    result = {"status": "available", "rate": known[-1][1], "as_of": known[-1][0], "history": known}
    if known[-1][0] != latest_day:
        result.update(status="stale", latest_decision=latest_day,
                      reason=f"Latest decision ({latest_day}) was published without a rate value")
    previous = known[-2][1] if len(known) > 1 else None
    result["last_decision"] = {"date": known[-1][0], "rate": known[-1][1],
                               "change_bp": round((known[-1][1] - previous) * 100) if previous is not None else None}
    return result


def priced_move(move_bp: float | None, as_of: date | None, today: date, approx: bool, method: str) -> dict[str, Any]:
    if move_bp is None or as_of is None:
        return {"status": "unavailable", "reason": "No pricing", "method": method}
    if today - as_of > MAX_PRICING_AGE:
        return {"status": "hidden", "reason": f"Pricing stale (as of {as_of})", "as_of": as_of, "method": method}
    if abs(move_bp) > MAX_PRICED_MOVE_BP:
        return {"status": "hidden", "reason": f"|move| {abs(move_bp):.0f}bp > {MAX_PRICED_MOVE_BP}bp", "as_of": as_of,
                "method": method}
    return {"status": "approximate" if approx else "available", "move_bp": round(move_bp, 1), "as_of": as_of,
            "approx": approx, "method": method}


def config_rate(bank: str) -> tuple[float | None, date | None]:
    raw = (yaml.safe_load(MEETINGS_PATH.read_text(encoding="utf-8")) or {}).get(bank, {})
    as_of = raw.get("current_rate_as_of")
    rate = raw.get("current_rate")
    return (float(rate) if rate is not None else None, date.fromisoformat(str(as_of)) if as_of else None)


# ── Data access ────────────────────────────────────────────────────────

async def decision_rows(bank: str) -> list[tuple[date, float | None]]:
    country, name = DECISIONS[bank]
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT DISTINCT ON (r.released_at::date) r.released_at::date AS d, r.actual::float AS rate
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = :c AND i.canonical_name = :n AND r.released_at <= now()
            ORDER BY r.released_at::date, (r.actual IS NULL), r.retrieved_at DESC, r.id DESC
        """), {"c": country, "n": name})
        return [(r.d, r.rate) for r in rows]


async def two_year_yield(bank: str) -> tuple[float, date] | None:
    async with get_sessionmaker()() as session:
        row = (await session.execute(text("""
            SELECT yield_value::float AS y, market_observation_date AS d FROM government_yield_observations
            WHERE country_code = :c AND maturity = '2Y' AND quality_status = 'valid' AND NOT is_outlier
            ORDER BY market_observation_date DESC, ingested_at DESC, id DESC LIMIT 1
        """), {"c": TWO_YEAR[bank]})).first()
        return (row.y, row.d) if row else None


async def _curve_date(bank: str) -> date | None:
    async with get_sessionmaker()() as session:
        return (await session.execute(text("SELECT MAX(curve_date) FROM ois_cache WHERE bank = :b"),
                                      {"b": bank})).scalar_one_or_none()


async def _priced_next_move(bank: str, today: date) -> dict[str, Any]:
    if bank == "ECB":
        async with get_sessionmaker()() as session:
            approx = await ecb_pricing.get_ecb_yield_approximation(session)
        three = next((h for h in approx["horizons"] if h["horizon"] == "3M"), {})
        return priced_move(three.get("implied_move_bp"), three.get("as_of"), today, True, approx["label"])
    method = "Fed funds futures, de-averaged per meeting" if bank == "FED" else "Existing curve proxy (ois_cache)"
    probabilities = await compute_meeting_probabilities(bank, n_meetings=1)
    if not probabilities or probabilities[0].data_state != DATA_STATE_LIVE:
        return {"status": "unavailable", "reason": probabilities[0].data_state_message if probabilities else "No curve",
                "method": method}
    return priced_move(probabilities[0].delta_bps, await _curve_date(bank), today, bank != "FED", method)


async def _gap(bank: str) -> dict[str, Any]:
    if bank == "FED":
        return {"kind": "bp", **await fed_regime.get_gap()}
    if bank == "ECB":
        return {"kind": "qualitative", **await ecb_regime.get_gap()}
    return {"kind": "pending", "status": "pending", "reason": "Gap model pending for this bank"}


async def get_summary(bank: str, today: date | None = None) -> dict[str, Any]:
    bank = bank.upper()
    if bank not in SUPPORTED_BANKS:
        raise ValueError(f"Unknown bank {bank}")
    today = today or date.today()
    cfg = bank_config()[bank]
    decisions = summarize_decisions(await decision_rows(bank))
    if decisions["status"] != "available":
        rate, as_of = config_rate(bank)
        decisions = {**decisions, "rate": rate, "rate_source": "config", "rate_as_of": as_of}
    else:
        decisions["rate_source"] = "decision"
    if bank == "FED":
        regime = await fed_regime.get_regime(today)
    elif decisions["status"] == "available":
        regime = {**classify_regime(decisions["history"], today), "method": "Fed v1 rate-level and last-move rules"}
    else:
        regime = {"regime": "unavailable", "reason": decisions.get("reason")}
    meetings = await get_upcoming_meetings(bank, 4)
    meeting_days = [datetime.fromisoformat(str(m["meeting_dt"])).date() for m in meetings]
    recent = [d for d, _ in decisions.get("history", [])][-1:]   # a just-passed meeting can still be in blackout
    two_year = await two_year_yield(bank)
    rate = decisions.get("rate")
    spread = None
    if two_year and rate is not None:
        spread = {"two_year_pct": two_year[0], "policy_pct": rate, "spread_bp": round((two_year[0] - rate) * 100),
                  "as_of": two_year[1], "country": TWO_YEAR[bank]}
    return {
        "bank": bank, "name": cfg["name"], "currency": cfg["currency"], "as_of": today,
        "policy_rate": {k: v for k, v in decisions.items() if k != "history"},
        "regime": regime.get("regime", "unavailable"), "regime_detail": regime,
        "next_meeting": {"date": meeting_days[0], "official": meetings[0].get("is_official", True)} if meetings else None,
        "blackout": blackout_status(bank, recent + meeting_days, today),
        "priced_next_move": await _priced_next_move(bank, today),
        "two_year_minus_policy": spread or {"status": "unavailable"},
        "gap": await _gap(bank),
    }
