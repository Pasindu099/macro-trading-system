"""Read models for the seven Overview panels. No upstream score calculations live here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.settings import get_settings
from app.services import ecb_regime, fed_regime, positioning, situations, verdict
from app.services.curve_metrics import get_curve
from app.services.macro_state import get_macro_state_board
from app.services.meeting_calendar import get_upcoming_meetings

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
BANKS = {"USD": "FED", "EUR": "ECB", "GBP": "BOE", "JPY": "BOJ", "AUD": "RBA",
         "NZD": "RBNZ", "CAD": "BOC", "CHF": "SNB"}
COUNTRIES = {"USD": "US", "EUR": "DE", "GBP": "UK", "JPY": "JP", "AUD": "AU",
             "NZD": "NZ", "CAD": "CA", "CHF": "CH"}
HIERARCHY = ("geopolitics / risk sentiment", "CB divergence", "data",
             "Yield curves", "positioning", "Event catalysts")


async def _board() -> dict:
    async with get_sessionmaker()() as session:
        return await get_macro_state_board(session)


def _pair_rows(scores: dict[str, float], pairs: list[dict]) -> list[dict]:
    rows = []
    for item in pairs:
        base, quote = item["base"], item["quote"]
        a, b = scores.get(base), scores.get(quote)
        rows.append({"pair": item["pair"], "overall": round(a - b, 2) if a is not None and b is not None else None,
                     "policy": "available" if {base, quote} == {"EUR", "USD"} else "pending"})
    return sorted(rows, key=lambda row: abs(row["overall"] or 0), reverse=True)


def _news_tag(headline: str) -> str:
    lower = headline.lower()
    for needle, label in (("inflation", "Inflation"), ("cpi", "Inflation"), ("rate", "Policy"),
                          ("central bank", "Policy"), ("oil", "Energy"), ("brent", "Energy"),
                          ("jobs", "Labor"), ("payroll", "Labor"), ("yield", "Rates")):
        if needle in lower:
            return label
    return "Macro"


async def hero() -> dict:
    episodes = await situations.get_situation_episodes(active=True)
    views = [await verdict.get_verdict(currency) for currency in ("USD", "EUR")]
    available = [view for view in views if view.get("status") == "available"]
    if not available and not episodes:
        return {"state": "pending", "message": "Dominant theme pending desk verdicts."}
    risk = next((row for row in episodes if row["situation_id"] == "risk_off"), None)
    if risk:
        theme = "Risk sentiment dominates FX while risk-off conditions persist."
        driver = "geopolitics / risk sentiment"
    else:
        lead = max(available, key=lambda row: abs(row["score"])) if available else None
        driver = lead["dominant_driver"] if lead else "Event catalysts"
        theme = (f"{lead['currency']} has a {lead['bias'].replace('_', ' ')} one-month bias. "
                 f"{lead['dominant_driver'].capitalize()} is the dominant driver.") if lead else "Active situations are shaping the macro backdrop."
    return {"state": "ok", "theme": theme, "driver": driver, "hierarchy": HIERARCHY}


async def active_situations() -> dict:
    rows = await situations.get_situation_episodes(active=True)
    for row in rows:
        key = row["scope_key"]
        row["href"] = (f"/desks/{key}" if key in ("USD", "EUR") else
                       f"/pairs?pair={key}" if row["scope"] == "pair" else "/desks")
    return {"state": "ok" if rows else "empty", "rows": rows,
            "message": "No active situations."}


async def moving_markets() -> dict:
    since = datetime.now(UTC) - timedelta(hours=24)
    async with get_sessionmaker()() as session:
        alerts = (await session.execute(text("""
            SELECT headline, source, url, detected_at AS seen_at, severity
            FROM intelligence.news_alerts WHERE detected_at >= :since
            ORDER BY detected_at DESC LIMIT 15
        """), {"since": since})).mappings().all()
        raw = (await session.execute(text("""
            SELECT title AS headline, source, url, published_at AS seen_at, NULL::text AS severity
            FROM raw_news WHERE published_at >= :since AND is_gated_relevant
            ORDER BY published_at DESC LIMIT 15
        """), {"since": since})).mappings().all()
    seen = set()
    rows = []
    for item in sorted([dict(row) for row in (*alerts, *raw)], key=lambda row: row["seen_at"], reverse=True):
        title = item["headline"].strip()
        if title.lower() in seen:
            continue
        seen.add(title.lower())
        rows.append({**item, "tag": _news_tag(title)})
    return {"state": "ok" if rows else "empty", "rows": rows[:8],
            "ai_paused": not get_settings().news_ai_enabled,
            "message": "No high-impact items in the last 24 hours."}


async def currency_ranking() -> dict:
    board = await _board()
    by_currency = {row["currency"]: row for row in board["rows"]}
    crowding = {row["currency"]: row for row in (await positioning.get_crowding())["currencies"]}
    desk_views = {currency: await verdict.get_verdict(currency) for currency in ("USD", "EUR")}
    regimes = {"USD": await fed_regime.get_regime(), "EUR": await ecb_regime.get_regime()}
    rows = []
    for currency in CURRENCIES:
        macro = by_currency.get(currency)
        curve = await get_curve(COUNTRIES[currency], "1M")
        cot = crowding.get(currency, {}).get("leveraged_funds", {})
        view = desk_views.get(currency)
        gap_raw = view.get("why", {}).get("gap", {}).get("raw") if view else None
        gap = (gap_raw.get("direction") or gap_raw.get("gap_bp")) if isinstance(gap_raw, dict) else None
        rows.append({"currency": currency, "macro": macro["overall_score"] if macro else None,
                     "date": macro["date"] if macro else None, "cot": cot.get("percentile"),
                     "curve": curve.get("regime", {}).get("label") if curve.get("regime", {}).get("status") == "available" else None,
                     "verdict": view, "enabled": currency in desk_views, "gap": gap,
                     "cb_regime": regimes[currency].get("regime") if currency in regimes else None})
    rows.sort(key=lambda row: (row["macro"] is None, -(row["macro"] or 0)))
    return {"state": "ok" if any(row["macro"] is not None for row in rows) else "pending", "rows": rows}


async def central_bank_map() -> dict:
    regimes = {"USD": await fed_regime.get_regime(), "EUR": await ecb_regime.get_regime()}
    rows = []
    for currency in CURRENCIES:
        bank = BANKS[currency]
        meetings = await get_upcoming_meetings(bank, 1)
        regime = regimes.get(currency)
        rows.append({"currency": currency, "bank": bank, "regime": regime,
                     "meeting": meetings[0]["meeting_dt"] if meetings else None,
                     "state": "ok" if regime and regime.get("regime") != "unavailable" else "pending"})
    return {"state": "ok", "rows": rows}


async def pair_map() -> dict:
    board = await _board()
    scores = {row["currency"]: float(row["overall_score"]) for row in board["rows"]
              if row["overall_score"] is not None}
    pairs = yaml.safe_load(Path("config/pairs.yaml").read_text(encoding="utf-8"))["pairs"]
    rows = _pair_rows(scores, pairs)
    available = [abs(row["overall"]) for row in rows if row["overall"] is not None]
    concentration = round(sum(sorted(available, reverse=True)[:3]) / sum(available) * 100, 1) if sum(available) else None
    return {"state": "ok" if available else "pending", "rows": rows,
            "concentration": concentration}


async def key_events() -> dict:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text("""
            SELECT r.released_at, i.display_name, i.country_code, c.currency_code,
                   r.estimate::float AS estimate, r.previous::float AS previous
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            JOIN countries c ON c.code = i.country_code
            WHERE r.released_at > now() AND r.released_at <= now() + interval '7 days'
              AND i.importance = 1 AND r.is_latest
            ORDER BY r.released_at, i.display_name LIMIT 16
        """))).mappings().all()
    return {"state": "ok" if rows else "empty", "rows": [dict(row) for row in rows],
            "message": "No high-impact events in the next seven days."}


PANELS = {"hero": hero, "situations": active_situations, "news": moving_markets,
          "ranking": currency_ranking, "central-banks": central_bank_map,
          "pairs": pair_map, "events": key_events}
