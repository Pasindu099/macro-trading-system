"""Data for each currency-desk panel, read from existing services.

Every builder returns a template context with ``state``:
``ok`` · ``empty`` (source has no rows) · ``unavailable`` (source/series missing) ·
``pending`` (panel ships in a later step; never carries numbers).
Builders only shape existing data; no new analytics live here.
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services import cb_tracking, fed_projections, fed_regime
from app.services import positioning as positioning_service
from app.services.central_banks import get_cb_policy_data
from app.services.curve_metrics import get_curve
from app.services.dxy import DXY_PAIR
from app.services.event_innovation_feed import FeedFilters, build_event_innovation_feed
from app.services.macro_state import get_macro_state_board
from app.services.rate_probability import get_rate_probability_view
from app.settings import get_settings

HOT, COOL, TEXT, MUTED = "#f6b65a", "#8fc3ff", "#e6e9ef", "#9aa3b2"
RANGE_DAYS = {"1M": 22, "3M": 66, "6M": 130, "1Y": 252}
CURVE_WINDOWS = {"1W": 7, "1M": 30, "3M": 90}
PERF_WINDOWS = {"w": 7, "m": 30, "q": 91}
THEMES = ["Inflation", "Labor", "Growth"]
# Pair vs USD and whether a rising pair means a stronger USD.
USD_LEGS = {
    "EUR": ("EUR/USD", -1), "GBP": ("GBP/USD", -1), "JPY": ("USD/JPY", 1), "AUD": ("AUD/USD", -1),
    "NZD": ("NZD/USD", -1), "CAD": ("USD/CAD", 1), "CHF": ("USD/CHF", 1),
}
REGIME_READ = {
    "bear_flattener": ("Bear flattener", "up", "Front end rising fastest: the market is pricing more {cb} hikes. Supportive for {ccy}."),
    "bear_steepener": ("Bear steepener", "warn", "Long end rising fastest: a term-premium or fiscal story rather than {cb} pricing. Watch for yields up with {ccy} down."),
    "bull_steepener": ("Bull steepener", "down", "Front end falling fastest: the market is pricing cuts. Negative for {ccy}."),
    "bull_flattener": ("Bull flattener", "flat", "Long end falling fastest: growth worries and flight to safety. {ccy} reaction depends on risk sentiment."),
    "twist_steepener": ("Twist steepener", "warn", "Front end down, long end up: hike bets trimmed while longer-dated premium rises. Mixed for {ccy}."),
    "twist_flattener": ("Twist flattener", "up", "Front end up, long end down: hawkish {cb} with growth worries further out. Usually {ccy}-positive short term."),
}


def pending(step: int, what: str) -> dict[str, Any]:
    return {"state": "pending", "step": step, "message": f"{what} · Available after step {step}"}


def unavailable(message: str, **extra: Any) -> dict[str, Any]:
    return {"state": "unavailable", "message": message, **extra}


def empty(message: str, **extra: Any) -> dict[str, Any]:
    return {"state": "empty", "message": message, **extra}


def signed(value: float | None, digits: int = 1, suffix: str = "") -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{abs(value):.{digits}f}{suffix}"


def tone_color(value: float | None) -> str:
    return TEXT if not value else HOT if value > 0 else COOL


def chart_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str, separators=(",", ":"))


# ── Shared readers ────────────────────────────────────────────────────

async def fx_series(pairs: list[str]) -> dict[str, list[tuple[date, float]]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT DISTINCT ON (pair, observation_date) pair, observation_date, close_value::float AS close
            FROM fx_spot_observations
            WHERE pair = ANY(:pairs) AND quality_status = 'valid' AND NOT is_outlier
            ORDER BY pair, observation_date, ingested_at DESC, id DESC
        """), {"pairs": pairs})
        out: dict[str, list[tuple[date, float]]] = defaultdict(list)
        for r in rows:
            out[r.pair].append((r.observation_date, r.close))
    return out


async def yield_series(country: str, tenors: list[str], since: date) -> dict[str, dict[date, float]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT DISTINCT ON (maturity, market_observation_date)
                maturity, market_observation_date AS d, yield_value::float AS v
            FROM government_yield_observations
            WHERE country_code = :c AND maturity = ANY(:t) AND market_observation_date >= :since
              AND quality_status = 'valid' AND NOT is_outlier
            ORDER BY maturity, market_observation_date, ingested_at DESC, id DESC
        """), {"c": country, "t": tenors, "since": since})
        out: dict[str, dict[date, float]] = defaultdict(dict)
        for r in rows:
            out[r.maturity][r.d] = r.v
    return out


async def indicator_history(country: str, canonicals: list[str], since: date) -> dict[str, list[tuple[date, float]]]:
    """Latest published value per reference period (released, with an actual)."""
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT DISTINCT ON (i.canonical_name, COALESCE(r.period_start_date, r.released_at::date))
                i.canonical_name AS name, COALESCE(r.period_start_date, r.released_at::date) AS period,
                r.actual::float AS value
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = :c AND i.canonical_name = ANY(:names)
              AND r.actual IS NOT NULL AND r.released_at <= now()
              AND COALESCE(r.period_start_date, r.released_at::date) >= :since
            ORDER BY i.canonical_name, COALESCE(r.period_start_date, r.released_at::date),
                     r.released_at DESC, r.retrieved_at DESC, r.id DESC
        """), {"c": country, "names": canonicals, "since": since})
        out: dict[str, list[tuple[date, float]]] = defaultdict(list)
        for r in rows:
            out[r.name].append((r.period, r.value))
    return out


async def policy_history(country: str, indicator: str, since: date) -> list[tuple[date, float]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT DISTINCT ON (r.released_at::date) r.released_at::date AS d, r.actual::float AS v
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = :c AND i.canonical_name = :n AND r.actual IS NOT NULL
              AND r.released_at <= now()
            ORDER BY r.released_at::date, r.retrieved_at DESC, r.id DESC
        """), {"c": country, "n": indicator})
        points = [(r.d, r.v) for r in rows]
    # Keep the last decision before the window so the step line starts at the right level.
    before = [p for p in points if p[0] < since][-1:]
    return before + [p for p in points if p[0] >= since]


def value_on_or_before(points: list[tuple[date, float]], day: date) -> tuple[date, float] | None:
    i = bisect.bisect_right([d for d, _ in points], day) - 1
    return points[i] if i >= 0 else None


# ── Panels ────────────────────────────────────────────────────────────

async def panel_verdict(desk: dict, params: dict) -> dict[str, Any]:
    from app.services.verdict import get_verdict

    result = await get_verdict(desk["currency"])
    return {"state": "ok" if result["status"] == "available" else "unavailable",
            "message": result.get("reason"), "verdict": result}


async def panel_situations(desk: dict, params: dict) -> dict[str, Any]:
    from app.services.situations import get_situation_episodes
    from app.services.verdict import _applies

    episodes = [row for row in await get_situation_episodes(active=True) if _applies(row, desk["currency"])]
    panels = []
    for row in episodes:
        current = row["evidence"].get("current", {})
        if row["situation_id"] == "energy_shock":
            panels.append({"id": "inflation_driver", "name": "Inflation driver",
                           "facts": [("Brent 3m change", current.get("brent_3m_pct"), "%"),
                                     ("Headline minus core", current.get("headline_core_gap_pp"), "pp")]})
        elif row["situation_id"] == "fr_fiscal_stress":
            panels.append({"id": "french_fiscal", "name": "French fiscal stress",
                           "facts": [("OAT–Bund 10Y", current.get("oat_bund_10y_bp"), "bp"),
                                     ("20d change", current.get("oat_bund_20d_change_bp"), "bp")],
                           "unavailable": "BTP spread unavailable"})
    return {"state": "ok", "episodes": episodes, "evidence_panels": panels}


def _heat(v: float | None) -> dict[str, str]:
    if v is None:
        return {"label": "—", "style": f"color:{MUTED}"}
    a = min(abs(v) / 4, 1) * 0.75 + 0.1
    bg = f"rgba(242,163,58,{a:.2f})" if v >= 0 else f"rgba(90,169,255,{a:.2f})"
    return {"label": signed(v, 1, "%"), "style": f"background:{bg};color:{'#0b0e13' if a > 0.55 else TEXT}"}


def usd_change(points: list[tuple[date, float]], days: int, direction: int) -> float | None:
    """% change in USD vs the leg over `days` calendar days (positive = USD stronger)."""
    if not points:
        return None
    end_day, end = points[-1]
    start = value_on_or_before(points, end_day - timedelta(days=days))
    if not start or not start[1]:
        return None
    ratio = end / start[1] if direction > 0 else start[1] / end
    return (ratio - 1) * 100


async def panel_price(desk: dict, params: dict) -> dict[str, Any]:
    rng = params.get("range") if params.get("range") in RANGE_DAYS else "1Y"
    series = await fx_series([DXY_PAIR, *(p for p, _ in USD_LEGS.values())])
    dxy = series.get(DXY_PAIR, [])
    ctx: dict[str, Any] = {"range": rng, "ranges": list(RANGE_DAYS)}
    ctx["perf"] = [
        {"ccy": ccy, **{k: _heat(usd_change(series.get(pair, []), days, sign)) for k, days in PERF_WINDOWS.items()}}
        for ccy, (pair, sign) in USD_LEGS.items()
    ]
    if len(dxy) < 2:
        return unavailable("Computed DXY has no observations yet.", **ctx)
    values = [v for _, v in dxy]
    ma = [None if i < 199 else sum(values[i - 199:i + 1]) / 200 for i in range(len(values))]
    n = RANGE_DAYS[rng]
    view, ma_view = dxy[-n:], ma[-n:]
    last, first = view[-1][1], view[0][1]
    ctx.update({
        "state": "ok", "as_of": view[-1][0], "last": f"{last:.2f}",
        "change": signed((last / first - 1) * 100, 1, f"% ({rng})"), "change_color": HOT if last >= first else COOL,
        "trend": None if ma[-1] is None else ("above" if last >= ma[-1] else "below"),
        "chart": chart_json({"kind": "line", "dates": [d for d, _ in view], "series": [
            {"name": "200-day average", "data": [round(m, 3) if m else None for m in ma_view], "color": "#7c8698", "width": 1.5},
            {"name": "DXY", "data": [round(v, 3) for _, v in view], "color": TEXT, "width": 2},
        ]}),
    })
    return ctx


def _theme_bar(z: float) -> str:
    w = min(abs(z) / 3, 1) * 50
    left = 50 if z >= 0 else 50 - w
    return f"left:{left:.1f}%;width:{w:.1f}%;background:{'#f2a33a' if z >= 0 else '#5aa9ff'}"


async def panel_economy(desk: dict, params: dict) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        board = await get_macro_state_board(session)
    row = next((r for r in board.get("rows", []) if r.get("currency") == desk["currency"]), None)
    if row is None:
        return empty(f"Macro State has no {desk['currency']} scores yet.")
    themes = []
    for name, key in (("Inflation", "inflation_score"), ("Labor", "labor_score"), ("Growth", "growth_score")):
        z = row.get(key)
        z = float(z) if z is not None else None
        themes.append({"name": name, "z": signed(z, 1), "color": tone_color(z), "bar": _theme_bar(z) if z is not None else None})
    overall = float(row["overall_score"]) if row.get("overall_score") is not None else None
    return {"state": "ok", "themes": themes, "composite": signed(overall, 2), "composite_color": tone_color(overall),
            "as_of": row.get("date"), "source": board.get("source"), "confidence": row.get("confidence")}


# CB Tracking status per theme (Step 8): inflation = core PCE, labor = unemployment, growth = real GDP.
SEP_THEME_VARIABLE = {"Inflation": "core_pce_inflation", "Labor": "unemployment_rate", "Growth": "real_gdp"}
STATUS_LABEL = {"running_hot": ("Running hot", HOT), "running_cold": ("Running cold", COOL), "on_track": ("On track", TEXT)}


def _sep_status(theme: str, tracking: dict[str, Any]) -> dict[str, Any]:
    entry = tracking.get("variables", {}).get(SEP_THEME_VARIABLE[theme], {})
    label, color = STATUS_LABEL.get(entry.get("status"), ("Unavailable", MUTED))
    return {"sep": label, "sep_color": color, "sep_detail": entry.get("reason") or (
        f"{entry.get('series')} vs {entry.get('projection')}% projection" if entry.get("projection") is not None else "")}


async def panel_direction(desk: dict, params: dict) -> dict[str, Any]:
    tracking = await cb_tracking.get_tracking() if desk["cb"] == "FED" else {}
    async with get_sessionmaker()() as session:
        feed = await build_event_innovation_feed(
            session, FeedFilters(days=30, include_unscored=False, country_code=desk["country"], category=None))
    by_theme: dict[str, list[dict]] = defaultdict(list)
    for row in feed["rows"]:
        by_theme[row["category"]].append(row)
    if not any(by_theme.get(t) for t in THEMES):
        return empty("No scored releases in the last 30 days.")
    rows = []
    for theme in THEMES:
        items = by_theme.get(theme, [])
        avg = sum(r["initial"] for r in items) / len(items) if items else None
        live = sum(r["current"] for r in items) if items else None
        rows.append({"theme": theme, "count": len(items), "surprise": signed(avg, 2, "σ"), "surprise_color": tone_color(avg),
                     "live": signed(live, 2, "σ"), "live_color": tone_color(live), **_sep_status(theme, tracking)})
    return {"state": "ok", "rows": rows}


def _shift_months(day: date, months: int) -> date:
    y, m = divmod(day.month - 1 + months, 12)
    return date(day.year + y, m + 1, 1)


def series_stats(points: list[tuple[date, float]], unit: str) -> dict[str, Any]:
    """Latest / prior / change / 12m-ago from per-period values."""
    fmt = (lambda v: f"{round(v):,}k") if unit == "k" else (lambda v: f"{v:.1f}{unit}")
    latest_day, latest = points[-1]
    prior = points[-2][1] if len(points) > 1 else None
    year_ago_day = _shift_months(latest_day.replace(day=1), -12)
    year_ago = next((v for d, v in points if d.replace(day=1) == year_ago_day), None)
    change = latest - prior if prior is not None else None
    chg_unit = "pp" if unit == "%" else "k" if unit == "k" else ""
    return {
        "latest": fmt(latest), "latest_period": latest_day, "prev": fmt(prior) if prior is not None else "—",
        "chg": signed(change, 0 if unit == "k" else 1, chg_unit), "chg_color": tone_color(change),
        "year_ago": fmt(year_ago) if year_ago is not None else "—",
    }


PALETTE = ["#f2a33a", "#5aa9ff", "#c9d1dd", "#4fd1b5", "#b794f6"]


async def panel_keydata(desk: dict, params: dict) -> dict[str, Any]:
    since = _shift_months(date.today().replace(day=1), -24)
    canonicals = [s["canonical"] for c in desk["charts"] for s in c["series"] if s.get("canonical")]
    history = await indicator_history(desk["country"], canonicals, since)
    cards = []
    for chart in desk["charts"]:
        legend, series_out, all_dates = [], [], set()
        for i, s in enumerate(chart["series"]):
            pts = history.get(s.get("canonical") or "", [])
            color = PALETTE[i % len(PALETTE)] if not chart.get("bars") else "#3d4656"
            if not pts:
                legend.append({"name": s["name"], "canonical": s.get("canonical"), "available": False, "color": color})
                continue
            all_dates.update(d for d, _ in pts)
            legend.append({"name": s["name"], "canonical": s.get("canonical"), "available": True, "color": color,
                           **series_stats(pts, chart.get("unit", ""))})
            series_out.append({"name": s["name"], "points": pts, "color": color, "width": 2.5 if i == 0 else 1.5,
                               "bar": bool(chart.get("bars"))})
        if chart.get("three_month_avg") and series_out:
            pts = series_out[0]["points"]
            avg = [(d, sum(v for _, v in pts[max(0, j - 2):j + 1]) / len(pts[max(0, j - 2):j + 1])) for j, (d, _) in enumerate(pts)]
            series_out.append({"name": "3m average", "points": avg, "color": PALETTE[0], "width": 2.5, "bar": False})
            legend.append({"name": "3m average", "available": True, "color": PALETTE[0], **series_stats(avg, chart.get("unit", ""))})
        dates = sorted(all_dates)
        card = {"id": chart["id"], "title": chart["title"], "sub": chart["sub"], "legend": legend,
                "ref_label": chart.get("ref_label"),
                "state": "ok" if series_out else "unavailable",
                "missing": [l["name"] for l in legend if not l["available"]],
                "driver": None}
        if series_out:
            lookup = [dict(s["points"]) for s in series_out]
            card["headline"] = next(l["latest"] for l in legend if l["available"])
            card["x_start"], card["x_end"] = dates[0].strftime("%b %y"), dates[-1].strftime("%b %y")
            card["chart"] = chart_json({"kind": "indicator", "dates": dates, "ref": chart.get("ref"), "series": [
                {"name": s["name"], "data": [lookup[k].get(d) for d in dates], "color": s["color"],
                 "width": s["width"], "bar": s["bar"]} for k, s in enumerate(series_out)]})
        cards.append(card)
    return {"state": "ok", "cards": cards}


async def panel_country(desk: dict, params: dict) -> dict[str, Any]:
    from app.services.country_monitor import get_country_monitor

    result = await get_country_monitor(desk)
    return {"state": "ok" if result["status"] == "available" else "unavailable", **result}


VARIABLE_LABEL = {"federal_funds_rate": "Fed funds rate", "pce_inflation": "PCE inflation",
                  "core_pce_inflation": "Core PCE", "unemployment_rate": "Unemployment", "real_gdp": "Real GDP"}
FLAG_TEXT = {"tolerance": "Tolerance: inflation revised up without a higher rate path",
             "response": "Response: inflation and the rate path both revised up",
             "none": "No reaction-function signal"}


async def _projection_block(desk: dict) -> dict[str, Any]:
    if desk["cb"] != "FED":
        return pending(9, "Projections")
    revisions = await cb_tracking.get_revisions()
    if revisions.get("status") == "unavailable":
        return unavailable(revisions["reason"])
    rows_by_var: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in revisions["revisions"]:
        rows_by_var[r["variable"]][r["horizon"]] = r
    horizons = sorted({h for v in rows_by_var.values() for h in v}, key=lambda h: (h == "longer_run", h))[:2] + ["longer_run"]
    rows = []
    for variable in VARIABLE_LABEL:
        cells = []
        for h in horizons:
            r = rows_by_var.get(variable, {}).get(h)
            chg = r["median_change"] if r else None
            cells.append({"now": f"{r['median']:.1f}" if r else "—", "prev": f"{r['previous_median']:.1f}" if r else "",
                          "chg": signed(chg, 1) if chg else ("±0.0" if r else ""), "chg_color": tone_color(chg)})
        rows.append({"name": VARIABLE_LABEL[variable], "cells": cells})
    risk = await fed_projections.get_risk_balance()
    risks = []
    for variable, kinds in risk.get("variables", {}).items():
        rk, unc = kinds.get("risk"), kinds.get("uncertainty")
        if not rk:
            continue
        n = rk["participants"]
        risks.append({"name": VARIABLE_LABEL.get(variable, variable), "down": rk["lower_or_downside"],
                      "bal": rk["similar_or_balanced"], "up": rk["higher_or_upside"], "n": n,
                      "w_down": rk["lower_or_downside"] / n * 100, "w_bal": rk["similar_or_balanced"] / n * 100,
                      "w_up": rk["higher_or_upside"] / n * 100, "diffusion": signed(rk.get("diffusion"), 2),
                      "diffusion_color": tone_color(rk.get("diffusion")),
                      "uncertainty_higher": unc["higher_or_upside"] if unc else None})
    flag = revisions["reaction_function"]
    return {"state": "ok", "round": revisions["round"], "previous_round": revisions["previous_round"],
            "horizons": ["Longer run" if h == "longer_run" else h for h in horizons], "rows": rows,
            "flag": flag["flag"], "flag_text": FLAG_TEXT[flag["flag"]],
            "flag_detail": f"PCE {flag['horizon']} {signed(flag['inflation_revision'], 1)}pp, funds {signed(flag['funds_rate_revision'], 1)}pp",
            "risks": risks, "risk_state": None if risks else risk.get("reason", "No risk-balance data")}


async def panel_fedview(desk: dict, params: dict) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        data = await get_cb_policy_data(session)
    bank = next((b for b in data.get("banks", []) if str(b["bank"]).upper() == desk["cb"]), None)
    base = {"projections": await _projection_block(desk),
            "speakers": unavailable("Committee balance needs the speaker pipeline (not built yet).")}
    if bank is None or not bank.get("reports"):
        return unavailable(f"No analysed {desk['cb_name']} policy documents in the database.", **base)
    latest = bank["reports"][0]
    tone = latest.get("tone_score")
    assess = [{"theme": t, "view": latest.get(f"{t.lower()}_outlook") or "No assessment in the latest document."}
              for t in THEMES]
    return {"state": "ok", **base, "latest_date": latest["date"], "doc_type": latest["doc_type"],
            "tone": signed(tone, 2), "tone_color": tone_color(tone), "tone_change": latest.get("tone_change"),
            "assess": assess, "documents": bank["reports"][:5]}


REGIME_LABEL = {"qe": "QE", "near_zero": "Near zero", "cutting": "Cutting", "holding": "Holding", "hiking": "Hiking"}


async def panel_fedpath(desk: dict, params: dict) -> dict[str, Any]:
    if desk["cb"] != "FED":
        return pending(9, "Policy regime model")
    regime = await fed_regime.get_regime()
    if regime.get("regime") == "unavailable":
        return unavailable(regime.get("reason", "No policy-rate history"))
    marks = {"met": ("●", TEXT), "not_met": ("○", MUTED), "unavailable": ("–", MUTED)}
    transitions = []
    for t in regime["transitions"]:
        score = t["score"]
        conds = [{"text": c["name"], "mark": marks[c["status"]][0], "color": marks[c["status"]][1],
                  "detail": c.get("reason") or _cond_value(c)} for c in t["conditions"]]
        transitions.append({"title": t["title"], "conds": conds,
                            "score": f"{score['met']} of {score['available']} met" + (
                                f" · {score['total'] - score['available']} unavailable" if score["total"] > score["available"] else ""),
                            "color": HOT if score["met"] else MUTED})
    return {"state": "ok", "regime": regime["regime"], "ladder": [
        {"label": REGIME_LABEL[r], "active": r == regime["regime"], "unavailable": r == "qe"} for r in regime["ladder"]],
        "rate": regime.get("rate"), "last_move_date": regime.get("last_move_date"), "last_move_bp": regime.get("last_move_bp"),
        "transitions": transitions}


def _cond_value(c: dict[str, Any]) -> str:
    value = c.get("value")
    if isinstance(value, list):
        value = ", ".join(f"{v:g}" for v in value)
    return f"{value} vs {c['threshold']}" if value is not None else c.get("threshold", "")


async def panel_priced(desk: dict, params: dict) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        view = await get_rate_probability_view(desk["cb"], session)
    meetings = []
    for m in view.get("meetings", [])[:3]:
        live = m.get("market_data_available")
        pct = (lambda v: f"{v * 100:.0f}%" if live and v is not None else "—")
        meetings.append({"date": m["meeting_at"], "cut": pct(m.get("cut_prob")), "hold": pct(m.get("hold_prob")),
                         "hike": pct(m.get("hike_prob")), "rate": f"{m['implied_rate']:.2f}%" if live else "—",
                         "data_state": m.get("data_state")})
    since = date.today() - timedelta(days=730)
    two = (await yield_series(desk["curve_country"], ["2Y"], since)).get("2Y", {})
    policy = await policy_history(desk["country"], desk["policy_indicator"], since)
    ctx: dict[str, Any] = {"meetings": meetings, "market": view.get("market_data", {}), "current_rate": view.get("current_rate"),
                           "current_rate_source": view.get("current_rate_source"), "current_rate_as_of": view.get("current_rate_as_of")}
    if not meetings:
        ctx.update(empty("No upcoming meetings in the calendar."))
    else:
        ctx["state"] = "ok"
    if two:
        dates = sorted(two)
        ctx["chart"] = chart_json({"kind": "line", "dates": dates, "series": [
            {"name": f"{desk['cb_short']} funds", "step": True, "color": "#7c8698", "width": 2,
             "data": [(value_on_or_before(policy, d) or (None, None))[1] for d in dates]},
            {"name": "2Y yield", "color": "#5aa9ff", "width": 2.5, "data": [two[d] for d in dates]},
        ]})
        ctx["two_last"] = f"{two[dates[-1]]:.2f}%"
        ctx["policy_last"] = f"{policy[-1][1]:.2f}%" if policy else "—"
    return ctx


async def panel_curve(desk: dict, params: dict) -> dict[str, Any]:
    window = params.get("window") if params.get("window") in CURVE_WINDOWS else "1M"
    curve = await get_curve(desk["curve_country"], window)
    ctx: dict[str, Any] = {"window": window, "windows": list(CURVE_WINDOWS)}
    if curve.get("status") != "available":
        return unavailable(curve.get("reason", "Curve unavailable"), **ctx)
    since = date.today() - timedelta(days=760)
    ys = await yield_series(desk["curve_country"], ["2Y", "10Y", "30Y"], since)
    as_of = curve["as_of"]
    anchor_day = as_of - timedelta(days=CURVE_WINDOWS[window])
    tenors, now_pts, then_pts = [], [], []
    says = {"2Y": f"{desk['cb_short']} expectations", "10Y": "Growth and inflation outlook", "30Y": "Term premium and fiscal credibility"}
    changes: dict[str, float | None] = {}
    for tenor in ("2Y", "10Y", "30Y"):
        pts = sorted(ys.get(tenor, {}).items())
        now = value_on_or_before(pts, as_of)
        then = value_on_or_before(pts, anchor_day)
        chg = (now[1] - then[1]) * 100 if now and then else None
        changes[tenor] = chg
        now_pts.append(now[1] if now else None)
        then_pts.append(then[1] if then else None)
        tenors.append({"tenor": tenor, "yld": f"{now[1]:.2f}%" if now else "—", "chg": signed(chg, 0, "bp"),
                       "chg_color": tone_color(chg), "says": says[tenor]})
    regime = curve["regime"]
    label, tone, read = REGIME_READ.get(regime.get("label"), (None, "flat", "")) if regime.get("status") == "available" else (None, "flat", "")
    stat = lambda item, note: {"value": signed(item.get("value_bp"), 0, "bp") if item.get("status") == "available" else "—",
                               "note": note if item.get("status") == "available" else item.get("reason", "Unavailable"),
                               "color": tone_color(item.get("value_bp")) if item.get("status") == "available" else MUTED}
    two_ten = curve["two_ten"]
    curve_stats = [
        {"label": "2s10s", **stat(two_ten, "Inverted" if curve["is_inverted"] else "Positive")},
        {"label": "10s30s", **stat(curve["ten_thirty"], "Long-end premium")},
        {"label": f"2Y − {desk['cb_short']} funds", **stat(curve["two_policy"],
            "Market expects further hikes" if (curve["two_policy"].get("value_bp") or 0) > 0 else "Market expects cuts")},
        {"label": "Real 10Y (10Y − breakeven)", "value": "—", "note": "Breakeven data not sourced", "color": MUTED},
    ]
    two = ys.get("2Y", {})
    ten = ys.get("10Y", {})
    s210_dates = sorted(set(two) & set(ten))[-520:]
    uninv = curve.get("latest_uninversion")
    d2, d30 = changes.get("2Y"), changes.get("30Y")
    dxy = (await fx_series([DXY_PAIR])).get(DXY_PAIR, [])
    dxy_chg = None
    if dxy:
        start = value_on_or_before(dxy, dxy[-1][0] - timedelta(days=CURVE_WINDOWS[window]))
        dxy_chg = (dxy[-1][1] / start[1] - 1) * 100 if start else None
    checks = [
        {"name": "Curve inversion", "status": "Active" if curve["is_inverted"] else "Not active",
         "detail": "2s10s below zero" if curve["is_inverted"] else "2s10s positive"},
        {"name": "Un-inversion after long inversion", "status": "Watch" if uninv else "Not active",
         "detail": f"Last un-inverted {uninv:%d %b %Y} after ≥90 days inverted" if uninv else "No qualifying un-inversion"},
        {"name": "Bear steepening (fiscal / term premium)",
         "status": "Watch" if d2 is not None and d30 is not None and d30 > d2 and d30 > 0 else "Not active",
         "detail": f"30Y {signed(d30, 0, 'bp')} vs 2Y {signed(d2, 0, 'bp')} over {window}"},
        {"name": f"Yields up + {desk['currency']} down",
         "status": "Watch" if d2 is not None and dxy_chg is not None and d2 > 0 and dxy_chg < 0 else "Not active",
         "detail": f"Over {window}: 2Y {signed(d2, 0, 'bp')}, DXY {signed(dxy_chg, 1, '%')}"},
    ]
    ctx.update({
        "state": "ok", "as_of": as_of, "regime_label": label, "regime_tone": tone,
        "regime_read": read.format(cb=desk["cb_short"], ccy=desk["currency"]) if read else regime.get("reason", ""),
        "regime_tenors": "2Y vs 10Y", "tenors": tenors, "curve_stats": curve_stats, "checks": checks,
        "uninversion": uninv,
        "policy_last": f"{curve['two_policy']['policy_rate']:.2f}%" if curve["two_policy"].get("status") == "available" else None,
        "curve_chart": chart_json({"kind": "curve", "tenors": ["2Y", "10Y", "30Y"], "series": [
            {"name": f"{window} ago", "data": then_pts, "color": "#5aa9ff", "dashed": True, "width": 2},
            {"name": "Today", "data": now_pts, "color": "#f2a33a", "width": 3}],
            "ref": curve["two_policy"].get("policy_rate")}),
        "s210_chart": chart_json({"kind": "line", "dates": s210_dates, "ref": 0, "shade_below": True,
            "mark_date": uninv, "series": [{"name": "2s10s (bp)", "color": TEXT, "width": 2,
            "data": [round((ten[d] - two[d]) * 100, 1) for d in s210_dates]}]}),
    })
    return ctx


async def panel_gap(desk: dict, params: dict) -> dict[str, Any]:
    if desk["cb"] != "FED":
        return pending(9, "Projections vs market gap")
    gap = await fed_regime.get_gap()
    if gap.get("status") == "unavailable":
        return unavailable(gap["reason"])
    dots = await fed_projections.get_dots()
    horizons = sorted(dots.get("horizons", {}), key=lambda h: (h == "longer_run", h))
    labels = ["Longer run" if h == "longer_run" else h for h in horizons]
    market = {r["horizon"]: r["market"] for r in gap["gaps"]}
    chart = chart_json({"kind": "dots", "categories": labels,
                        "dots": [[i, d["rate"], d["participants"]] for i, h in enumerate(horizons) for d in dots["horizons"][h]],
                        "median": [dots["medians"].get(h) for h in horizons],
                        "market": [market.get(h) for h in horizons]})
    lead = next((r for r in gap["gaps"] if r["gap_bp"] is not None), None)
    tilt = gap["tilt"]["flag"]
    return {"state": "ok", "round": gap["round"], "rows": gap["gaps"], "chart": chart, "method": gap["method"],
            "participants": max(dots.get("participants", {}).values(), default=None),
            "headline": (f"SEP {lead['horizon']} median {signed(lead['gap_bp'], 0, 'bp')} vs market" if lead else "Gap unavailable"),
            "headline_color": tone_color(lead["gap_bp"]) if lead else MUTED,
            "tilt": {"hawkish_risk": "Hawkish risk", "dovish_risk": "Dovish risk", "neutral": "Neutral"}[tilt],
            "tilt_color": {"hawkish_risk": HOT, "dovish_risk": COOL}.get(tilt, TEXT), "tilt_basis": gap["tilt"]["basis"]}


def _pbar(pct: float | None, color: str) -> str:
    return f"width:{max(0, min(100, pct or 0)):.0f}%;background:{color}"


async def panel_positioning(desk: dict, params: dict) -> dict[str, Any]:
    crowding = await positioning_service.get_crowding("3y")
    squeeze = await positioning_service.get_squeeze("3y")
    c = next((r for r in crowding["currencies"] if r["currency"] == desk["currency"]), None)
    s = next((r for r in squeeze["currencies"] if r["currency"] == desk["currency"]), None)
    if c is None:
        return empty("No COT positioning for this currency.")
    rows = []
    for category, label in (("leveraged_funds", "COT leveraged funds net (3y pctile)"),
                            ("asset_manager", "COT asset managers net (3y pctile)")):
        item = c.get(category, {})
        pct = item.get("percentile")
        if pct is None:
            rows.append({"label": label, "value": "Unavailable", "color": MUTED, "bar": _pbar(0, "#7c8698")})
            continue
        crowd = item.get("crowding")
        color = HOT if crowd == "crowded_long" else COOL if crowd == "crowded_short" else TEXT
        crowd_label = {"crowded_long": " · Crowded long", "crowded_short": " · Crowded short"}.get(crowd, "")
        sq = (s or {}).get(category, {}).get("status")
        rows.append({"label": label, "value": f"{pct:.0f}th{crowd_label}", "color": color,
                     "bar": _pbar(pct, "#f2a33a" if crowd == "crowded_long" else "#5aa9ff" if crowd == "crowded_short" else "#7c8698"),
                     "squeeze": sq, "net": item.get("net"), "report_date": item.get("report_date")})
    return {"state": "ok", "rows": rows}


# indicators.importance: 1 = high, 2 = medium, 3 = low (config/bundle_config.yaml).
IMPACT = {1: "High", 2: "Medium", 3: "Low"}


async def upcoming_events(country: str) -> list[Any]:
    from app.api.routes.public import list_calendar_events  # calendar query lives with its route

    async with get_sessionmaker()() as session:
        return await list_calendar_events(session, days_back=0, days_forward=60, country_code=country, limit=200)


async def panel_catalysts(desk: dict, params: dict) -> dict[str, Any]:
    events = await upcoming_events(desk["country"])
    now = datetime.now(UTC)
    upcoming = [e for e in events if e.released_at and e.released_at > now][:10]
    if not upcoming:
        return empty(f"No upcoming {desk['currency']} events in the calendar.")
    return {"state": "ok", "events": [{"date": e.released_at, "event": e.display_name + (f" ({e.period})" if e.period else ""),
                                       "impact": IMPACT.get(e.importance, "Low")} for e in upcoming]}


async def news_alerts(currency: str) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""
            SELECT headline, url, source, detected_at, implied_tier, severity, alert_text
            FROM intelligence.news_alerts
            WHERE :ccy = ANY(affected_currencies)
            ORDER BY detected_at DESC LIMIT 40
        """), {"ccy": currency})
        return [dict(r) for r in rows.mappings().all()]


async def panel_news(desk: dict, params: dict) -> dict[str, Any]:
    rows = await news_alerts(desk["currency"])
    types = sorted({r["implied_tier"] for r in rows if r["implied_tier"]})
    selected = params.get("type") if params.get("type") in types else "All"
    items = [dict(r, type_label=str(r["implied_tier"] or "Other").replace("_", " ").title())
             for r in rows if selected == "All" or r["implied_tier"] == selected][:12]
    ctx = {"filters": [{"value": "All", "label": "All"}] + [{"value": t, "label": t.replace("_", " ").title()} for t in types],
           "selected": selected, "ai_paused": not get_settings().news_ai_enabled}
    if not rows:
        return empty(f"No {desk['currency']} news items stored.", **ctx)
    return {"state": "ok", "news": items, **ctx}


async def panel_scenarios(desk: dict, params: dict) -> dict[str, Any]:
    from app.services.scenarios import get_scenarios

    result = await get_scenarios(desk["currency"])
    return {"state": "ok" if result["status"] == "available" else "unavailable",
            "message": result.get("reason"), **result}


@dataclass(frozen=True)
class Panel:
    id: str
    anchor: str
    n: str
    label: str
    wide: bool
    builder: Callable[[dict, dict], Awaitable[dict[str, Any]]]


# Mockup order. `n`/`label` drive the question-chain nav.
PANELS: list[Panel] = [
    Panel("verdict", "verdict", "", "Verdict", True, panel_verdict),
    Panel("situations", "situations", "", "Situations", True, panel_situations),
    Panel("price", "q0", "0", "Price", True, panel_price),
    Panel("economy", "q1", "1", "Economy", False, panel_economy),
    Panel("direction", "q2", "2", "Direction", False, panel_direction),
    Panel("keydata", "data", "", "Key data", True, panel_keydata),
    Panel("country", "country", "", "Country monitor", True, panel_country),
    Panel("fedview", "fedview", "3", "{cb} view", True, panel_fedview),
    Panel("fedpath", "q3", "4", "{cb} path", False, panel_fedpath),
    Panel("priced", "q4", "5", "Priced", False, panel_priced),
    Panel("curve", "curve", "5b", "Curve", True, panel_curve),
    Panel("gap", "q5", "6", "Gap", True, panel_gap),
    Panel("positioning", "q6", "7", "Positioning", False, panel_positioning),
    Panel("catalysts", "q7", "8", "Catalysts", False, panel_catalysts),
    Panel("news", "usdnews", "·", "News", True, panel_news),
    Panel("scenarios", "q8", "9", "Scenarios", True, panel_scenarios),
]
PANELS_BY_ID = {p.id: p for p in PANELS}
